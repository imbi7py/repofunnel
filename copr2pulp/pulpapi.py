"""pulpapi - Access to Pulp REST API"""
from rest_framework.response import Response
from rest_framework.reverse import reverse as reverse_absolute
from rest_framework import renderers
from rest_framework import serializers
from rest_framework import status
from rest_framework import viewsets
import requests

#TODO: Configurable Pulp connectivity. For now, assume a local container
# service link called "pulpapi", and don't verify the HTTPS connection

#=====================================
# Client access to the remote Pulp API
#=====================================

pulp_url = "https://pulpapi/pulp"
pulp_api_path = "/api/v2/"
pulp_api_url = pulp_url + pulp_api_path
pulp_repo_url = "http://localhost/pulp/repos/"

def _access_pulp_url(http_method, *api_segments, **request_kwds):
    url_segments = [pulp_api_url]
    url_segments.extend(api_segments)
    api_url = "/".join(url_segments) + "/"
    reply = http_method(api_url,
                        auth=('admin', 'admin'),
                        verify=False,
                        **request_kwds)
    reply.raise_for_status()
    return reply

def _get_pulp_url(*api_segments, **request_kwds):
    return _access_pulp_url(requests.get, *api_segments, **request_kwds)

def _post_pulp_url(*api_segments, **request_kwds):
    return _access_pulp_url(requests.post, *api_segments, **request_kwds)

def _delete_pulp_url(*api_segments, **request_kwds):
    return _access_pulp_url(requests.delete, *api_segments, **request_kwds)

def _convert_repo(repo):
    repo_id = repo["id"]
    return {"repo_id": repo_id,
            "display_name": repo["display_name"],
            "details": repo,
           }

def iter_repos():
    pulp_info = _get_pulp_url("repositories")
    if pulp_info.text == "not found":
        raw_repos = []
    else:
        raw_repos = pulp_info.json()
    for repo in raw_repos:
        yield _convert_repo(repo)

def get_repo(repo_id):
    return _convert_repo(_get_pulp_url("repositories", repo_id).json())

def create_repo(repo_id, display_name):
    details = {"id":repo_id, "display_name":display_name}
    pulp_reply = _post_pulp_url("repositories", json=details)
    return _convert_repo(pulp_reply.json())

def delete_repo(repo_id):
    try:
        return _delete_pulp_url("repositories", repo_id).json()
    except requests.HTTPError as e:
        if e.response.status_code == status.HTTP_404_NOT_FOUND:
            return {"error": None, "result": None, "spawned_tasks": []}
        raise

def set_feed(repo_id, feed_url=None):
    config = {"importer_type_id": "yum_importer"}
    if feed_url is not None:
        config["importer_config"] = {"feed": feed_url}
    pulp_reply = _post_pulp_url("repositories", repo_id, "importers",
                                json=config)
    return pulp_reply.json()

def get_feed(repo_id):
    pulp_reply = _get_pulp_url("repositories", repo_id, "importers")
    return pulp_reply.json()[0]

def start_sync(repo_id):
    pulp_reply = _post_pulp_url("repositories", repo_id, "actions/sync")
    return pulp_reply.json()

def start_merge(source_repo_id, target_repo_id):
    config = {"source_repo_id": source_repo_id}
    pulp_reply = _post_pulp_url("repositories", target_repo_id,
                                "actions/associate", json=config)
    return pulp_reply.json()

def set_target(repo_id):
    config = {
        "distributor_id": "target",
        "distributor_type_id": "yum_distributor",
        "distributor_config": {
            "http": True,
            "https": False, # No default SSL certs set up
            "relative_url": repo_id,
        },
        "auto_publish": True
    }
    pulp_reply = _post_pulp_url("repositories", repo_id, "distributors",
                                json=config)
    distributor = pulp_reply.json()
    repo_url = pulp_repo_url + distributor["config"]["relative_url"]
    return distributor, repo_url

def get_target(repo_id):
    pulp_reply = _get_pulp_url("repositories", repo_id, "distributors")
    return pulp_reply.json()[0]

def start_publish(repo_id):
    config = {"id": "target"}
    pulp_reply = _post_pulp_url("repositories", repo_id, "actions/publish",
                                json=config)
    return pulp_reply.json()

# TODO: Pass through properly structured error information
class RemotePulpError(Exception): pass

# TODO: Switch from requests to aiohttp and make this non-blocking
def wait_for_task(task_id):
    while True:
        pulp_reply = _get_pulp_url("tasks", task_id)
        reply_data = pulp_reply.json()
        status = reply_data["state"]
        if status == "finished":
            break
        if status not in ("waiting", "running", "suspended"):
            raise RemotePulpError(str(reply_data))
    return reply_data

#=========================================
# Local REST API proxy for remote Pulp API
#=========================================

COLLECTION_NAME = 'pulp_repo'
def add_to_router(router, prefix):
    router.register(prefix, PulpRepoViewSet, base_name=COLLECTION_NAME)

# Adapts HyperlinkedIdentityField to use __getitem__ rather than __getattr__
class DictBasedIdentityField(serializers.HyperlinkedIdentityField):

    def get_url(self, obj, view_name, request, format):
        # Unsaved objects will not yet have a valid URL.
        try:
            lookup_value = obj[self.lookup_field]
        except KeyError:
            return None
        kwargs = {self.lookup_url_kwarg: lookup_value}
        return self.reverse(view_name, kwargs=kwargs, request=request, format=format)


class PulpRepoSerializer(serializers.Serializer):
    url = DictBasedIdentityField(view_name='pulp_repo-detail',
                                 lookup_field='repo_id',
                                 lookup_url_kwarg='pk')
    repo_id = serializers.CharField()
    display_name = serializers.CharField()
    details = serializers.DictField(read_only=True)

    def create(self, validated_data):
        repo = pulpapi.create_repo(**validated_data)
        return repo

class PulpRepoViewSet(viewsets.ViewSet):
    serializer_class = PulpRepoSerializer

    def _serialize(self, data, request, *, many=False):
        context={'request': request}
        return self.serializer_class(data, context=context, many=many).data

    def list(self, request):
        return Response(self._serialize(iter_repos(), request, many=True))

    def retrieve(self, request, *, pk):
        return Response(self._serialize(get_repo(pk), request))

    def create(self, request):
        serializer = self.serializer_class(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors,
                            status=status.HTTP_400_BAD_REQUEST)
        repo = serializer.save()
        return Response(self._serialize(repo, request),
                        status=status.HTTP_201_CREATED)
