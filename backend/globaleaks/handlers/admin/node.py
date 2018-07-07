# -*- coding: utf-8
#
#   /admin/node
#   *****
# Implementation of the code executed on handler /admin/node
from six import text_type

from twisted.internet.defer import inlineCallbacks, returnValue

from globaleaks import models, utils, LANGUAGES_SUPPORTED_CODES, LANGUAGES_SUPPORTED
from globaleaks.db import db_refresh_memory_variables
from globaleaks.db.appdata import load_appdata
from globaleaks.handlers.base import BaseHandler
from globaleaks.models.config import ConfigFactory, NodeL10NFactory
from globaleaks.orm import transact
from globaleaks.rest import errors, requests
from globaleaks.state import State
from globaleaks.utils.utility import log, parse_csv_ip_ranges_to_ip_networks

def db_admin_serialize_node(session, tid, language, config_node='admin_node'):
    config = ConfigFactory(session, tid, config_node).serialize()

    # Contexts and Receivers relationship
    configured = session.query(models.ReceiverContext).filter(models.ReceiverContext.context_id == models.Context.id,
                                                              models.Context.tid).count() > 0

    misc_dict = {
        'languages_supported': LANGUAGES_SUPPORTED,
        'languages_enabled': models.EnabledLanguage.list(session, tid),
        'configured': configured,
        'root_tenant': tid == 1,
        'https_possible': tid == 1 or State.tenant_cache[1].reachable_via_web,
    }

    if tid != 1:
        root_tenant_node = ConfigFactory(session, 1, 'node')
        misc_dict['version'] = root_tenant_node.get_val(u'version')
        misc_dict['latest_version'] = root_tenant_node.get_val(u'latest_version')
        misc_dict['enable_footer_customization'] = root_tenant_node.get_val(u'enable_footer_customization')

    l10n_dict = NodeL10NFactory(session, tid).localized_dict(language)

    return utils.sets.merge_dicts(config, misc_dict, l10n_dict)


@transact
def admin_serialize_node(session, tid, language, config_node='admin_node'):
    return db_admin_serialize_node(session, tid, language, config_node)


def db_update_enabled_languages(session, tid, languages_enabled, default_language):
    cur_enabled_langs = models.EnabledLanguage.list(session, tid)
    new_enabled_langs = [text_type(y) for y in languages_enabled]

    if len(new_enabled_langs) < 1:
        raise errors.InputValidationError("No languages enabled!")

    if default_language not in new_enabled_langs:
        raise errors.InputValidationError("Invalid lang code for chosen default_language")

    appdata = None
    for lang_code in new_enabled_langs:
        if lang_code not in LANGUAGES_SUPPORTED_CODES:
            raise errors.InputValidationError("Invalid lang code: %s" % lang_code)

        if lang_code not in cur_enabled_langs:
            if appdata is None:
                appdata = load_appdata()
            log.debug("Adding a new lang %s" % lang_code)
            models.config.add_new_lang(session, tid, lang_code, appdata)

    to_remove = list(set(cur_enabled_langs) - set(new_enabled_langs))
    if to_remove:
        session.query(models.User).filter(models.User.tid == tid, models.User.language.in_(to_remove)).update({'language': default_language}, synchronize_session='fetch')
        session.query(models.EnabledLanguage).filter(models.EnabledLanguage.tid == tid, models.EnabledLanguage.name.in_(to_remove)).delete(synchronize_session='fetch')

@transact
def update_enabled_languages(session, tid, languages_enabled, default_language):
    return db_update_enabled_languages(session, tid, languages_enabled, default_language)


def db_update_node(session, tid, request, language, config_node):
    """
    Update and serialize the node infos

    :param session: the session on which perform queries.
    :param language: the language in which to localize data
    :return: a dictionary representing the serialization of the node
    """
    node = ConfigFactory(session, tid, config_node)

    node.update(request)

    if 'basic_auth' in request:
        if request['basic_auth'] and request['basic_auth_username'] and request['basic_auth_password']:
            node.set_val(u'basic_auth', True)
            node.set_val(u'basic_auth_username', request['basic_auth_username'])
            node.set_val(u'basic_auth_password', request['basic_auth_password'])
        else:
            node.set_val(u'basic_auth', False)

    # Validate that IP addresses/ranges we're getting are goo
    if 'ip_filter_authenticated' in request:
        if request['ip_filter_authenticated_enable'] and request['ip_filter_authenticated']:
            # Make sure we can validate and parse the whole thing
            parse_csv_ip_ranges_to_ip_networks(request['ip_filter_authenticated'])

    if 'languages_enabled' in request and 'default_language' in request:
        db_update_enabled_languages(session,
                                    tid,
                                    request['languages_enabled'],
                                    request['default_language'])

    if language in models.EnabledLanguage.list(session, tid):
        node_l10n = NodeL10NFactory(session, tid)
        node_l10n.update(request, language)

    db_refresh_memory_variables(session, [tid])

    return db_admin_serialize_node(session, tid, language)


@transact
def update_node(*args):
    return db_update_node(*args)

class NodeInstance(BaseHandler):
    check_roles =  {'admin', 'receiver', 'custodian'}
    cache_resource = True
    invalidate_cache = True

    @inlineCallbacks
    def determine_allow_config_filter(self):
        """Determines what filters are allowed, else throws invalid authentication"""
        if self.current_user.user_role == 'admin':
            node = ('admin_node', requests.AdminNodeDesc)
        else:
            yield self.can_edit_general_settings_or_raise()
            node = ('general_settings', requests.GeneralSettingsDesc)

        returnValue(node)

    @inlineCallbacks
    def get(self):
        """
        Get the node infos.
        """

        config_node = yield self.determine_allow_config_filter()
        serialized_node = yield admin_serialize_node(self.request.tid,
                                                     self.request.language,
                                                     config_node=config_node[0])
        returnValue(serialized_node)

    @inlineCallbacks
    def put(self):
        """
        Update the node infos.
        """

        config_node = yield self.determine_allow_config_filter()

        request = yield self.validate_message(self.request.content.read(),
                                              config_node[1])

        serialized_node = yield update_node(self.request.tid,
                                            request,
                                            self.request.language,
                                            config_node[0])
        returnValue(serialized_node)
