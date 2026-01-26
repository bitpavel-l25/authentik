# How to run:
# ak shell
# from authentik.providers.scim.custom import run_import
# run_import()

from django.db.models.query import Q
from authentik.providers.scim.models import SCIMProvider
from authentik.core.models import User
from authentik.core.models import Group
from authentik.providers.scim.clients.schema import User as SCIMUserSchema
from authentik.providers.scim.clients.schema import Group as SCIMGroupSchema
from authentik.providers.scim.clients import (
    SCIMGroupClient,
    SCIMUserClient
)

class AWSSCIMImporter():
    def __init__(self, provider_pk):
        self.provider = SCIMProvider.objects.filter(
            Q(backchannel_application__isnull=False) | Q(application__isnull=False),
            pk=provider_pk,
        ).first()
        if not self.provider:
            print("No provider found. Is it assigned to an application?")
            return
        self.client_group = SCIMGroupClient(self.provider)
        self.client_user = SCIMUserClient(self.provider)

    def run(self):
        with self.provider.sync_lock as lock_acquired:
            if not lock_acquired:
                print("Synchronization is already running. Skipping.")
                return
        self.import_users()
        self.import_groups()

    def import_users(self):
        # pass
        remote_user_ids = []
        rsp, nextCursor = self._get_aws_paged_user_ids("")
        remote_user_ids += rsp
        while nextCursor:
            rsp, nextCursor = self._get_aws_paged_user_ids(nextCursor)
            remote_user_ids += rsp
        for remote_user in remote_user_ids:
            user = User.objects.filter(username=remote_user['username']).first()
            scim_user = self.client_user.to_schema(user, None)



    def _get_scim_paged_users(self, cursor):
        remote_user_ids = []
        rsp = self._request(
            "GET",
            "/Users",
            params={
                "cursor": cursor,
            },
        )
        for user in rsp["Resources"]:
            # scim_user = SCIMUserSchema.model_validate(user)
            rsp_user = {
                'username': user['userName'],
                'scim_id': user['id']
            }
            remote_user_ids.append(rsp_user)
        if "nextCursor" in rsp:
            return remote_user_ids, rsp["nextCursor"]
        else:
            return remote_user_ids, None

    def _get_scim_paged_groups(self, cursor):
        remote_group_ids = []
        rsp = self._request(
            "GET",
            "/Groups",
            params={
                "cursor": cursor,
            },
        )
        for group in rsp["Resources"]:
            scim_group = SCIMGroupSchema.model_validate(group)
            remote_group_ids.append(scim_group.id)
        if "nextCursor" in rsp:
            return remote_group_ids, rsp["nextCursor"]
        else:
            return remote_group_ids, None


# from authentik.core.models import User
# from authentik.core.models import Group
# from authentik.providers.scim.clients.base import SCIMClient

# from authentik.providers.scim.clients.schema import Group as SCIMGroupSchema
# from authentik.providers.scim.clients.schema import User as SCIMUserSchema

# from authentik.providers.scim.models import (
#     SCIMProvider,
#     SCIMProviderGroup,
#     SCIMProviderUser,
# )


# class AWSSCIMGroupImporter():
#     def __init__(self, provider_pk):





# class AWSSCIMGroupImporter(SCIMClient[Group, SCIMProviderGroup, SCIMGroupSchema]):
#     def __init__(self, provider_pk):


# def run_import():
#     print('SCIM IMPORT IS STARTED')
#     remote_user_ids = []
#     rsp, nextCursor = self._get_aws_paged_user_ids("")
#     remote_user_ids += rsp
#     while nextCursor:
#         rsp, nextCursor = self._get_aws_paged_user_ids(nextCursor)
#         remote_user_ids += rsp



# def _get_aws_paged_user_ids(self, cursor):
#     remote_user_ids = []
#     rsp = self._request(
#         "GET",
#         "/Users",
#         params={
#             "cursor": cursor,
#         },
#     )
#     for user in rsp["Resources"]:
#         scim_user = SCIMUserSchema.model_validate(user)
#         remote_user_ids.append(scim_user.id)
#     if "nextCursor" in rsp:
#         return remote_user_ids, rsp["nextCursor"]
#     else:
#         return remote_user_ids, None
