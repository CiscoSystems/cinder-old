# vim: tabstop=4 shiftwidth=4 softtabstop=4

#    Copyright (c) 2011 Justin Santa Barbara
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import httplib
import urlparse

from cinder.openstack.common import log as logging
from cinder.openstack.common import jsonutils


LOG = logging.getLogger(__name__)


class OpenStackApiException(Exception):
    def __init__(self, message=None, response=None):
        self.response = response
        if not message:
            message = 'Unspecified error'

        if response:
            _status = response.status
            _body = response.read()

            message = _('%(message)s\nStatus Code: %(_status)s\n'
                        'Body: %(_body)s') % locals()

        super(OpenStackApiException, self).__init__(message)


class OpenStackApiAuthenticationException(OpenStackApiException):
    def __init__(self, response=None, message=None):
        if not message:
            message = _("Authentication error")
        super(OpenStackApiAuthenticationException, self).__init__(message,
                                                                  response)


class OpenStackApiAuthorizationException(OpenStackApiException):
    def __init__(self, response=None, message=None):
        if not message:
            message = _("Authorization error")
        super(OpenStackApiAuthorizationException, self).__init__(message,
                                                                  response)


class OpenStackApiNotFoundException(OpenStackApiException):
    def __init__(self, response=None, message=None):
        if not message:
            message = _("Item not found")
        super(OpenStackApiNotFoundException, self).__init__(message, response)


class TestOpenStackClient(object):
    """Simple OpenStack API Client.

    This is a really basic OpenStack API client that is under our control,
    so we can make changes / insert hooks for testing

    """

    def __init__(self, auth_user, auth_key, auth_uri):
        super(TestOpenStackClient, self).__init__()
        self.auth_result = None
        self.auth_user = auth_user
        self.auth_key = auth_key
        self.auth_uri = auth_uri
        # default project_id
        self.project_id = 'openstack'

    def request(self, url, method='GET', body=None, headers=None):
        _headers = {'Content-Type': 'application/json'}
        _headers.update(headers or {})

        parsed_url = urlparse.urlparse(url)
        port = parsed_url.port
        hostname = parsed_url.hostname
        scheme = parsed_url.scheme

        if scheme == 'http':
            conn = httplib.HTTPConnection(hostname,
                                          port=port)
        elif scheme == 'https':
            conn = httplib.HTTPSConnection(hostname,
                                           port=port)
        else:
            raise OpenStackApiException("Unknown scheme: %s" % url)

        relative_url = parsed_url.path
        if parsed_url.query:
            relative_url = relative_url + "?" + parsed_url.query
        LOG.info(_("Doing %(method)s on %(relative_url)s") % locals())
        if body:
            LOG.info(_("Body: %s") % body)

        conn.request(method, relative_url, body, _headers)
        response = conn.getresponse()
        return response

    def _authenticate(self):
        if self.auth_result:
            return self.auth_result

        auth_uri = self.auth_uri
        headers = {'X-Auth-User': self.auth_user,
                   'X-Auth-Key': self.auth_key,
                   'X-Auth-Project-Id': self.project_id}
        response = self.request(auth_uri,
                                headers=headers)

        http_status = response.status
        LOG.debug(_("%(auth_uri)s => code %(http_status)s") % locals())

        if http_status == 401:
            raise OpenStackApiAuthenticationException(response=response)

        auth_headers = {}
        for k, v in response.getheaders():
            auth_headers[k] = v

        self.auth_result = auth_headers
        return self.auth_result

    def api_request(self, relative_uri, check_response_status=None, **kwargs):
        auth_result = self._authenticate()

        # NOTE(justinsb): httplib 'helpfully' converts headers to lower case
        base_uri = auth_result['x-server-management-url']

        full_uri = '%s/%s' % (base_uri, relative_uri)

        headers = kwargs.setdefault('headers', {})
        headers['X-Auth-Token'] = auth_result['x-auth-token']

        response = self.request(full_uri, **kwargs)

        http_status = response.status
        LOG.debug(_("%(relative_uri)s => code %(http_status)s") % locals())

        if check_response_status:
            if not http_status in check_response_status:
                if http_status == 404:
                    raise OpenStackApiNotFoundException(response=response)
                elif http_status == 401:
                    raise OpenStackApiAuthorizationException(response=response)
                else:
                    raise OpenStackApiException(
                                        message=_("Unexpected status code"),
                                        response=response)

        return response

    def _decode_json(self, response):
        body = response.read()
        LOG.debug(_("Decoding JSON: %s") % (body))
        if body:
            return jsonutils.loads(body)
        else:
            return ""

    def api_get(self, relative_uri, **kwargs):
        kwargs.setdefault('check_response_status', [200])
        response = self.api_request(relative_uri, **kwargs)
        return self._decode_json(response)

    def api_post(self, relative_uri, body, **kwargs):
        kwargs['method'] = 'POST'
        if body:
            headers = kwargs.setdefault('headers', {})
            headers['Content-Type'] = 'application/json'
            kwargs['body'] = jsonutils.dumps(body)

        kwargs.setdefault('check_response_status', [200, 202])
        response = self.api_request(relative_uri, **kwargs)
        return self._decode_json(response)

    def api_put(self, relative_uri, body, **kwargs):
        kwargs['method'] = 'PUT'
        if body:
            headers = kwargs.setdefault('headers', {})
            headers['Content-Type'] = 'application/json'
            kwargs['body'] = jsonutils.dumps(body)

        kwargs.setdefault('check_response_status', [200, 202, 204])
        response = self.api_request(relative_uri, **kwargs)
        return self._decode_json(response)

    def api_delete(self, relative_uri, **kwargs):
        kwargs['method'] = 'DELETE'
        kwargs.setdefault('check_response_status', [200, 202, 204])
        return self.api_request(relative_uri, **kwargs)

    def get_volume(self, volume_id):
        return self.api_get('/volumes/%s' % volume_id)['volume']

    def get_volumes(self, detail=True):
        rel_url = '/volumes/detail' if detail else '/volumes'
        return self.api_get(rel_url)['volumes']

    def post_volume(self, volume):
        return self.api_post('/volumes', volume)['volume']

    def delete_volume(self, volume_id):
        return self.api_delete('/volumes/%s' % volume_id)
