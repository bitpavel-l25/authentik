"""User client"""

from typing import Any

from django.db import transaction
from django.utils.http import urlencode
from orjson import dumps
from pydantic import ValidationError

from authentik.core.models import User
from authentik.lib.merge import MERGE_LIST_UNIQUE
from authentik.lib.sync.mapper import PropertyMappingManager
from authentik.lib.sync.outgoing.exceptions import ObjectExistsSyncException, StopSync
from authentik.policies.utils import delete_none_values
from authentik.providers.scim.clients.base import SCIMClient
from authentik.providers.scim.clients.schema import SCIM_USER_SCHEMA
from authentik.providers.scim.clients.schema import User as SCIMUserSchema
from authentik.providers.scim.models import (
    SCIMCompatibilityMode,
    SCIMMapping,
    SCIMProvider,
    SCIMProviderUser
)


class SCIMUserClient(SCIMClient[User, SCIMProviderUser, SCIMUserSchema]):
    """SCIM client for users"""

    connection_type = SCIMProviderUser
    connection_type_query = "user"
    mapper: PropertyMappingManager

    def __init__(self, provider: SCIMProvider):
        super().__init__(provider)
        self.mapper = PropertyMappingManager(
            self.provider.property_mappings.all().order_by("name").select_subclasses(),
            SCIMMapping,
            ["provider", "connection"],
        )

    def to_schema(self, obj: User, connection: SCIMProviderUser) -> SCIMUserSchema:
        """Convert authentik user into SCIM"""
        raw_scim_user = super().to_schema(obj, connection)
        try:
            scim_user = SCIMUserSchema.model_validate(delete_none_values(raw_scim_user))
        except ValidationError as exc:
            raise StopSync(exc, obj) from exc
        if SCIM_USER_SCHEMA not in scim_user.schemas:
            scim_user.schemas.insert(0, SCIM_USER_SCHEMA)
        # As this might be unset, we need to tell pydantic it's set so ensure the schemas
        # are included, even if its just the defaults
        scim_user.schemas = list(scim_user.schemas)
        if not scim_user.externalId:
            scim_user.externalId = str(obj.uid)
        return scim_user

    def delete(self, obj: User):
        """Delete user"""
        scim_user = SCIMProviderUser.objects.filter(provider=self.provider, user=obj).first()
        if not scim_user:
            self.logger.debug("User does not exist in SCIM, skipping")
            return None
        response = self._request("DELETE", f"/Users/{scim_user.scim_id}")
        scim_user.delete()
        return response

    def create(self, user: User):
        """Create user from scratch and create a connection object"""
        scim_user = self.to_schema(user, None)
        with transaction.atomic():
            try:
                response = self._request(
                    "POST",
                    "/Users",
                    json=scim_user.model_dump(
                        mode="json",
                        exclude_unset=True,
                    ),
                )
            except ObjectExistsSyncException as exc:
                if not self._config.filter.supported:
                    raise exc
                users = self._request(
                    "GET",
                    f"/Users?{urlencode({'filter': f'userName eq \"{scim_user.userName}\"'})}",
                )
                users_res = users.get("Resources", [])
                if len(users_res) < 1:
                    raise exc
                return SCIMProviderUser.objects.create(
                    provider=self.provider,
                    user=user,
                    scim_id=users_res[0]["id"],
                    attributes=users_res[0],
                )
            else:
                scim_id = response.get("id")
                if not scim_id or scim_id == "":
                    raise StopSync("SCIM Response with missing or invalid `id`")
                return SCIMProviderUser.objects.create(
                    provider=self.provider, user=user, scim_id=scim_id, attributes=response
                )

    def diff(self, local_created: dict[str, Any], connection: SCIMProviderUser):
        """Check if a user is different than what we last wrote to the remote system.
        Returns true if there is a difference in data."""
        local_known = connection.attributes
        local_updated = {}
        MERGE_LIST_UNIQUE.merge(local_updated, local_known)
        MERGE_LIST_UNIQUE.merge(local_updated, local_created)
        return dumps(local_updated) != dumps(local_known)

    def update(self, user: User, connection: SCIMProviderUser):
        """Update existing user"""
        scim_user = self.to_schema(user, connection)
        scim_user.id = connection.scim_id
        payload = scim_user.model_dump(
            mode="json",
            exclude_unset=True,
        )
        if not self.diff(payload, connection):
            self.logger.debug("Skipping user write as data has not changed")
            return
        response = self._request(
            "PUT",
            f"/Users/{connection.scim_id}",
            json=payload,
        )
        connection.attributes = response
        connection.save()


    def cleanup(self):
        self.logger.warning("RUN USER CLEANUP")
        if self.provider.compatibility_mode == SCIMCompatibilityMode.AWS:
            self.logger.warning("AWS TRUE")
        else:
            self.logger.warning("AWS FALSE")
                        
        remote_user_ids = {}
        match self.provider.compatibility_mode:
            case SCIMCompatibilityMode.AWS:
                rsp, nextCursor = self._get_aws_paged_user_ids('')
                remote_user_ids.update(rsp)
                while nextCursor:
                    rsp, nextCursor = self._get_aws_paged_user_ids(nextCursor)
                    remote_user_ids.update(rsp)
            case _: #TODO: to implement
                return
        if len(remote_user_ids) < 1:
            return
        self.logger.warning("REMOTE USER IDS", ids=remote_user_ids) # TODO: to remove

        # for id in remote_user_ids.values():
            # SCIMProviderUser.objects.filter(provider=self.provider, scim_id=id).first()
            # list(SCIMProviderUser.objects.filter(provider=self.provider).values_list("scim_id", flat=True))

        # local_user_ids_old = list(
        #     SCIMProviderGroup.objects.filter(
        #         user__pk__in=remote_user_ids.keys(), provider=self.provider
        #     ).values_list("scim_id", flat=True)
        # )
        # self.logger.warning("LOCAL GROUP IDS OLD", ids=local_user_ids_old)
        # for id in remote_user_ids.values():
        #     if id not in local_user_ids_old:
        #         self._request("DELETE", f"/Groups/{id}")

        # local_user_ids_raw = list(
        #     self.provider.get_object_qs(User).values_list("scimprovideruser", flat=True) # uuid
        # )
        # self.logger.warning("LOCAL USER IDS RAW", ids=local_user_ids_raw)
        # local_user_ids = [str(i) for i in local_user_ids_raw]

        local_user_ids = list(SCIMProviderUser.objects.filter(provider=self.provider).values_list("scim_id", flat=True))

        self.logger.warning("LOCAL USER IDS", ids=local_user_ids)

        for id in remote_user_ids.keys():
            if id not in local_user_ids:
                self.logger.warning("SCIM DELETE USER", id=remote_user_ids[id])
                # self._request("DELETE", f"/Groups/{remote_user_ids[id]}")



    def _get_aws_paged_user_ids(self, cursor):
        remote_user_ids = {}
        rsp = self._request(
            "GET",
            "/Users",
            params = {
                'cursor': cursor,
            }
        )
        for user in rsp['Resources']:
            scim_user = SCIMUserSchema.model_validate(user)
            if scim_user.externalId in scim_user:
                self.logger.error(
                    "SCIM user with conflicting External ID is found",
                    external_id=scim_user.externalId,
                    scim_id=scim_user.id
                )
            else:
                remote_user_ids[scim_user.externalId] = scim_user.id
        if 'nextCursor' in rsp:
            return remote_user_ids, rsp['nextCursor']
        else:
            return remote_user_ids, None

    # def cleanup(self):
    #     self.logger.warning("RUN USERS CLEANUP")
    #     if self.provider.compatibility_mode == SCIMCompatibilityMode.AWS:
    #         self.logger.warning("TRUE")
    #     else:
    #         self.logger.warning("FALSE")
                        
    #     # remote_group_ids = {}
    #     # match self.provider.compatibility_mode:
    #     #     case SCIMCompatibilityMode.AWS:
    #     #         rsp = self._request(
    #     #             "GET",
    #     #             "/Groups",
    #     #             params = {
    #     #                 'cursor': '',
    #     #             }
    #     #         )
    #     #         for group in rsp['Resources']:
    #     #             scim_group = SCIMGroupSchema.model_validate(group)
    #     #             if scim_group.externalId in scim_group:
    #     #                 self.logger.error(
    #     #                     "SCIM group with conflicting External ID is found",
    #     #                     external_id=scim_group.externalId,
    #     #                     scim_id=scim_group.id
    #     #                 )
    #     #             else:
    #     #                 remote_group_ids[scim_group.externalId] = scim_group.id
    #     #         while 'nextCursor' in rsp:
    #     #             rsp = self._request(
    #     #                 "GET",
    #     #                 "/Groups",
    #     #                 params = {
    #     #                     'cursor': rsp['nextCursor'],
    #     #                 }
    #     #             )
    #     #             for group in rsp['Resources']:
    #     #                 # TODO: move to method
    #     #                 scim_group = SCIMGroupSchema.model_validate(group)
    #     #                 if scim_group.externalId in scim_group:
    #     #                     self.logger.error(
    #     #                         "SCIM group with conflicting External ID is found",
    #     #                         external_id=scim_group.externalId,
    #     #                         scim_id=scim_group.id
    #     #                     )
    #     #                 else:
    #     #                     remote_group_ids[scim_group.externalId] = scim_group.id
    #     # if len(remote_group_ids) < 1:
    #     #     return
    #     # local_group_ids = list(
    #     #     SCIMProviderGroup.objects.filter(
    #     #         group__pk__in=remote_group_ids.keys(), provider=self.provider
    #     #     ).values_list("scim_id", flat=True)
    #     # )
    #     # for id in remote_group_ids.values():
    #     #     if id not in local_group_ids:
    #     #         self._request("DELETE", f"/Groups/{id}")