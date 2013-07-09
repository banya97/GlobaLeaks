# -*- coding: UTF-8
#   node
#   ****
#
# Implementation of classes handling the HTTP request to /node, public
# exposed API.

from twisted.internet.defer import inlineCallbacks

from globaleaks import utils
from globaleaks.settings import transact
from globaleaks.handlers.base import BaseHandler
from globaleaks.handlers.authentication import transport_security_check
from globaleaks import models

@transact
def anon_serialize_node(store, default_lang):
    node = store.find(models.Node).one()

    # Contexts and Receivers relationship
    associated = store.find(models.ReceiverContext).count()

    return {
      'name': unicode(node.name),
      'description': utils.optlang(node.description, default_lang),
      'hidden_service': unicode(node.hidden_service),
      'public_site': unicode(node.public_site),
      'email': unicode(node.email),
      'languages_enabled': node.languages_enabled,
      'languages_supported': node.languages_supported,
      'configured': True if associated else False,
      # extended settings info:
      'maximum_namesize': node.maximum_namesize,
      'maximum_descsize': node.maximum_descsize,
      'maximum_textsize': node.maximum_textsize,
      'maximum_filesize': node.maximum_filesize,
      'tor2web_admin': node.tor2web_admin,
      'tor2web_submission': node.tor2web_submission,
      'tor2web_tip': node.tor2web_tip,
      'tor2web_receiver': node.tor2web_receiver,
      'tor2web_unauth': node.tor2web_unauth,
    }

def anon_serialize_context(context, default_lang):
    """
    @param context: a valid Storm object
    @return: a dict describing the contexts available for submission,
        (e.g. checks if almost one receiver is associated)
    """
    context_dict = {
        "receivers": []
    }

    for receiver in context.receivers:
        context_dict['receivers'].append(unicode(receiver.id))

    if not len(context_dict['receivers']):
        return None

    context_dict.update({
        "context_gus": unicode(context.id),
        "description": utils.optlang(context.description, default_lang),
        "escalation_threshold": None,
        "fields": list(context.fields or []),
        "file_max_download": int(context.file_max_download),
        "file_required": context.file_required,
        "name": utils.optlang(context.name, default_lang),
        "selectable_receiver": bool(context.selectable_receiver),
        "tip_max_access": int(context.tip_max_access),
        "tip_timetolive": int(context.tip_timetolive),
        "receipt_description": u'NYI', # unicode(context.receipt_description), # optlang
        "submission_introduction": u'NYI', # unicode(context.submission_introduction), # optlang
        "submission_disclaimer": u'NYI', # unicode(context.submission_disclaimer), # optlang
    })
    return context_dict


def anon_serialize_receiver(receiver, default_lang):
    """
    @param receiver: a valid Storm object
    @return: a dict describing the receivers available in the node
        (e.g. checks if almost one context is associated, or, in
         node where GPG encryption is enforced, that a valid key is registered)
    """
    receiver_dict = {
        "contexts": [],
    }

    for context in receiver.contexts:
        receiver_dict['contexts'].append(unicode(context.id))

    if not len(receiver_dict['contexts']):
        return None

    receiver_dict.update({
        "can_delete_submission": receiver.can_delete_submission,
        "creation_date": utils.pretty_date_time(receiver.creation_date),
        "update_date": utils.pretty_date_time(receiver.last_update),
        "description": utils.optlang(receiver.description, default_lang),
        "name": unicode(receiver.name),
        "receiver_gus": unicode(receiver.id),
        "receiver_level": int(receiver.receiver_level),
        "tags": receiver.tags,
    })
    return receiver_dict


class InfoCollection(BaseHandler):
    """
    U1
    Returns information on the GlobaLeaks node. This includes submission
    parameters (contexts description, fields, public receiver list).
    Contains System-wide properties.
    """

    @inlineCallbacks
    @transport_security_check("unauth")
    def get(self, *uriargs):
        """
        Parameters: None
        Response: publicNodeDesc
        Errors: NodeNotFound
        """
        response = yield anon_serialize_node(self.get_default_lang())
        self.finish(response)

# U2 Submission create
# U3 Submission update/status/delete
# U4 Files

class StatsCollection(BaseHandler):
    """
    U5
    Interface for the public statistics, configured between the Node settings and the
    Contexts settings
    """

    def get(self, *uriargs):
        """
        Parameters: None
        Response: publicStatsList
        Errors: StatsNotCollectedError

        This interface return the collected statistics for the public audience.
        """
        pass


@transact
def get_public_context_list(store, default_lang):
    context_list = []
    contexts = store.find(models.Context)

    for context in contexts:
        context_desc = anon_serialize_context(context, default_lang)
        # context not yet ready for submission return None
        if context_desc:
            context_list.append(context_desc)

    return context_list


class ContextsCollection(BaseHandler):
    """
    U6
    Return the public list of contexts, those information are shown in client
    and would be memorized in a third party indexer service. This is way some dates
    are returned within.
    """
    @inlineCallbacks
    @transport_security_check("unauth")
    def get(self, *uriargs):
        """
        Parameters: None
        Response: publicContextList
        Errors: None
        """
        response = yield get_public_context_list(self.get_default_lang())
        self.finish(response)

@transact
def get_public_receiver_list(store, default_lang):
    receiver_list = []
    receivers = store.find(models.Receiver)

    for receiver in receivers:
        receiver_desc = anon_serialize_receiver(receiver, default_lang)
        # receiver not yet ready for submission return None
        if receiver_desc:
            receiver_list.append(receiver_desc)

    return receiver_list

class ReceiversCollection(BaseHandler):
    """
    U7
    Return the description of all the receivers visible from the outside.
    """

    @inlineCallbacks
    @transport_security_check("unauth")
    def get(self, *uriargs):
        """
        Parameters: None
        Response: publicReceiverList
        Errors: None
        """
        response = yield get_public_receiver_list(self.get_default_lang())
        self.finish(response)

