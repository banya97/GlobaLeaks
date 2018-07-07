# -*- coding: utf-8 -*-
#
# Handlers dealing with platform authentication
import ipaddress

from random import SystemRandom
from six import text_type, binary_type
from sqlalchemy import and_, or_
from twisted.internet.defer import inlineCallbacks, returnValue

from globaleaks.utils import security
from globaleaks.handlers.base import BaseHandler, Sessions, new_session
from globaleaks.models import InternalTip, User
from globaleaks.orm import transact
from globaleaks.rest import errors, requests
from globaleaks.settings import Settings
from globaleaks.state import State
from globaleaks.utils.utility import datetime_now, deferred_sleep, log, parse_csv_ip_ranges_to_ip_networks

def random_login_delay():
    """
    in case of failed_login_attempts introduces
    an exponential increasing delay between 0 and 42 seconds

        the function implements the following table:
            ----------------------------------
           | failed_attempts |      delay     |
           | x < 5           | 0              |
           | 5               | random(5, 25)  |
           | 6               | random(6, 36)  |
           | 7               | random(7, 42)  |
           | 8 <= x <= 42    | random(x, 42)  |
           | x > 42          | 42             |
            ----------------------------------
    """
    failed_attempts = Settings.failed_login_attempts

    if failed_attempts >= 5:
        n = failed_attempts * failed_attempts

        min_sleep = failed_attempts if failed_attempts < 42 else 42
        max_sleep = n if n < 42 else 42

        return SystemRandom().randint(min_sleep, max_sleep)

    return 0


def db_get_wbtip_by_receipt(session, tid, receipt):
    hashed_receipt = security.hash_password(receipt, State.tenant_cache[tid].receipt_salt)
    return session.query(InternalTip) \
                  .filter(InternalTip.receipt_hash == text_type(hashed_receipt, 'utf-8'),
                          InternalTip.tid == tid).one_or_none()


@transact
def login_whistleblower(session, tid, receipt, client_using_tor):
    """
    login_whistleblower returns the InternalTip.id
    """
    wbtip = db_get_wbtip_by_receipt(session, tid, receipt)
    if wbtip is None:
        log.debug("Whistleblower login: Invalid receipt")
        Settings.failed_login_attempts += 1
        raise errors.InvalidAuthentication

    if not client_using_tor and not State.tenant_cache[tid]['https_whistleblower']:
        log.err("Denied login request over clear Web for role 'whistleblower'")
        raise errors.TorNetworkRequired

    log.debug("Whistleblower login: Valid receipt")

    wbtip.last_access = datetime_now()

    return wbtip.id


@transact
def login(session, tid, username, password, client_using_tor, client_ip, token=''):
    """
    login returns a tuple (user_id, state, pcn)
    """
    user = None

    if token:
        user = session.query(User).filter(User.auth_token == token,
                                          User.state != u'disabled',
                                          User.tid == tid).one_or_none()
    else:
        users = session.query(User).filter(User.username == username,
                                           User.state != u'disabled',
                                           (or_(and_(User.role == u'admin', User.tid.in_(set([1, tid]))),
                                               and_(User.role != u'admin', User.tid == tid))))
        for u in users:
            if security.check_password(password, u.salt, u.password):
                user = u

    if user is None:
        log.debug("Login: Invalid credentials")
        Settings.failed_login_attempts += 1
        raise errors.InvalidAuthentication

    if not client_using_tor and not State.tenant_cache[tid]['https_' + user.role]:
        log.err("Denied login request over Web for role '%s'" % user.role)
        raise errors.TorNetworkRequired

    # Check if we're doing IP address checks today
    if State.tenant_cache[tid]['ip_filter_authenticated_enable']:
        ip_networks = parse_csv_ip_ranges_to_ip_networks(
            State.tenant_cache[tid]['ip_filter_authenticated']
        )

        if isinstance(client_ip, binary_type):
            client_ip = client_ip.decode()

        client_ip_obj = ipaddress.ip_address(client_ip)

        # Safety check, we always allow localhost to log in
        success = False
        if client_ip_obj.is_loopback is True:
            success = True

        for ip_network in ip_networks:
            if client_ip_obj in ip_network:
                success = True

        if success is not True:
            raise errors.AccessLocationInvalid

    log.debug("Login: Success (%s)" % user.role)

    user.last_login = datetime_now()

    return user.id, user.state, user.role, user.password_change_needed


class AuthenticationHandler(BaseHandler):
    """
    Login handler for admins and recipents and custodians
    """
    check_roles = 'unauthenticated'
    uniform_answer_time = True

    @inlineCallbacks
    def post(self):
        request = self.validate_message(self.request.content.read(), requests.AuthDesc)

        delay = random_login_delay()
        if delay:
            yield deferred_sleep(delay)

        user_id, status, role, pcn = yield login(self.request.tid,
                                                 request['username'],
                                                 request['password'],
                                                 self.request.client_using_tor,
                                                 self.request.client_ip,
                                                 request['token'])

        session = new_session(self.request.tid, user_id, role, status)

        returnValue({
            'session_id': session.id,
            'role': session.user_role,
            'user_id': session.user_id,
            'session_expiration': int(session.getTime()),
            'status': session.user_status,
            'password_change_needed': pcn
        })

class ReceiptAuthHandler(BaseHandler):
    """
    Receipt handler used by whistleblowers
    """
    check_roles = 'unauthenticated'
    uniform_answer_time = True

    @inlineCallbacks
    def post(self):
        request = self.validate_message(self.request.content.read(), requests.ReceiptAuthDesc)

        receipt = request['receipt']

        delay = random_login_delay()
        if delay:
            yield deferred_sleep(delay)

        user_id = yield login_whistleblower(self.request.tid, receipt, self.request.client_using_tor)

        session = new_session(self.request.tid, user_id, 'whistleblower', 'Enabled')

        returnValue({
            'session_id': session.id,
            'role': session.user_role,
            'user_id': session.user_id,
            'session_expiration': int(session.getTime())
        })


class SessionHandler(BaseHandler):
    """
    Session handler for authenticated users
    """
    check_roles = {'admin','receiver','custodian','whistleblower'}

    def get(self):
        """
        Refresh and retrive session
        """
        return {
            'session_id': self.current_user.id,
            'role': self.current_user.user_role,
            'user_id': self.current_user.user_id,
            'session_expiration': int(self.current_user.getTime()),
            'status': self.current_user.user_status,
            'password_change_needed': False
        }

    def delete(self):
        """
        Logout
        """
        del Sessions[self.current_user.id]
