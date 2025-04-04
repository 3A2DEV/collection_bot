# https://github.com/octokit/octokit.net/issues/638#issuecomment-67795998

# FIXME
#   - [Errno -5] No address associated with hostname

import http.client
import logging
import requests
import socket
import sys
import time
import traceback

from requests.exceptions import ReadTimeout

from ansibullbot._text_compat import to_text
from ansibullbot.errors import RateLimitError
from ansibullbot.utils.sqlite_utils import AnsibullbotDatabase

import ansibullbot.constants as C


ADB = AnsibullbotDatabase()


def get_rate_limit():
    url = C.DEFAULT_GITHUB_URL
    if not url:
        url = 'https://api.github.com/rate_limit'
    else:
        url += '/rate_limit'
    token = C.DEFAULT_GITHUB_TOKEN

    if not token:
        logging.error('No GitHub token provided')
        return None

    while True:
        logging.debug(url)
        try:
            rr = requests.get(
                url,
                headers={'Authorization': 'Bearer %s' % token}
            )
            if rr.status_code == 401:
                logging.error('Invalid GitHub credentials')
                return None
            response = rr.json()
            break
        except Exception as e:
            logging.error('Rate limit check failed: %s', str(e))
            time.sleep(60)

    if 'resources' not in response or 'core' not in response.get('resources', {}):
        logging.warning('Unable to fetch rate limit: %s', response.get('message', 'unknown error'))
        return None

    ADB.set_rate_limit(token=token, rawjson=response)
    return response


def get_reset_time():
    '''Return the number of seconds until the rate limit resets'''

    # default to 62 minutes
    reset_time = 60 * 62

    rl = get_rate_limit()

    if rl:
        # The time at which the current rate limit window resets
        # in UTC epoch seconds. [ex. 1483405983]
        logging.debug('rate_limit: %s' % to_text(rl))
        reset_time = rl['resources']['core']['reset'] - time.time()
        reset_time = int(reset_time)
        if reset_time < 1:
            reset_time = 0

        # always pad by 5s
        reset_time += 5

    logging.debug('get_reset_time [return]: %s(s)' % reset_time)
    return reset_time


def RateLimited(fn):

    def inner(*args, **kwargs):

        # bypass this decorator for testing purposes
        if not C.DEFAULT_RATELIMIT:
            return fn(*args, **kwargs)

        success = False
        count = 0
        while not success:
            count += 1

            # use cached ratelimit data and a query counter to reduce api calls for rate_limit
            rl = ADB.get_rate_limit_rawjson(token=C.DEFAULT_GITHUB_TOKEN)
            qcounter = ADB.get_rate_limit_query_counter(token=C.DEFAULT_GITHUB_TOKEN)
            if rl is None or qcounter is None or qcounter > 100 or (rl and rl['resources']['core']['remaining'] < 100):
                rl = get_rate_limit()
                ADB.set_rate_limit(token=C.DEFAULT_GITHUB_TOKEN, rawjson=rl)
                qcounter = ADB.get_rate_limit_query_counter(token=C.DEFAULT_GITHUB_TOKEN)

            logging.debug('qcounter: %s' % qcounter)
            rl['resources']['core']['remaining'] -= qcounter

            if rl:
                logging.debug('ratelimited call #%s [%s] [%s] [%s]' %
                              (count,
                               type(args[0]),
                               fn.__name__,
                               rl['resources']['core']['remaining']))

            if count > 10:
                logging.error('HIT 10 loop iteration on call, giving up')
                sys.exit(1)

            # default to 5 minute sleep
            stime = 5*60
            try:
                x = fn(*args, **kwargs)
                success = True
            except RateLimitError:
                stime = get_reset_time()
            except OSError as e:
                logging.warning('socket error: sleeping 2 minutes %s' % e)
                time.sleep(2*60)
            except ReadTimeout as e:
                logging.warning('read timeout: sleeping 2 minutes %s' % e)
                time.sleep(2*60)
            except AttributeError as e:
                if "object has no attribute 'decoded_content'" in e.message:
                    stime = get_reset_time()
                    msg = 'decoded_content error: sleeping %s minutes %s' \
                        % (stime / 60, e)
                    logging.warning(msg)
                    time.sleep(stime)
                else:
                    raise Exception('unhandled message type')
            except TypeError as e:
                if "unsupported operand type(s) for -=" in e.message:
                    stime = get_reset_time()
                    msg = 'retry type error: sleeping %s minutes %s' \
                        % (stime / 60, e)
                    logging.warning(msg)
                    time.sleep(stime)
                else:
                    logging.error(e)
                    raise Exception('unhandled message type')
            except Exception as e:
                logging.error(e)
                if hasattr(e, 'data') and e.data is not None and e.data.get('message'):
                    msg = e.data.get('message')
                    if 'blocked from content creation' in msg:
                        logging.warning('content creation rate limit exceeded')
                        stime = 2*60
                    elif 'Label does not exist' in msg:
                        return None
                    elif 'rate limit exceeded' in msg:
                        logging.warning('general rate limit exceeded')
                        stime = get_reset_time()
                    elif isinstance(e, socket.error):
                        logging.warning('socket error')
                        stime = 5*60
                    elif 'Server Error' in msg:
                        logging.warning('server error')
                        stime = 2*60
                    elif 'Not Found' in msg:
                        logging.info('object not found')
                        return None
                    elif "object has no attribute 'decoded_content'" in msg:
                        # occurs most often when fetching file contents from
                        # the api such as the issue template
                        stime = get_reset_time()
                    elif 'No handler found for uri' in msg:
                        # Not sure what is happening here ...
                        # No handler found for uri
                        # [/repos/ansible/ansible/issues/14171] and method [GET]
                        stime = 2*60
                    elif msg.lower() == 'issues are disabled for this repo':
                        return None
                    elif 'The request could not be processed because too many files changed' in msg:
                        stime = 2*60
                    else:
                        raise Exception('unhandled message type')
                elif isinstance(e, http.client.IncompleteRead):
                    # https://github.com/ansible/ansibullbot/issues/593
                    stime = 2*60
                elif isinstance(e, http.client.BadStatusLine):
                    # https://github.com/ansible/ansibullbot/issues/602
                    stime = 2*60
                elif getattr(e, 'status', 0) >= 500:
                    # https://github.com/ansible/ansibullbot/issues/1025
                    # https://sentry.io/red-hat-ansibullbot/ansibullbot/issues/804854465
                    stime = 2*60
                else:
                    ex_type, ex, tb = sys.exc_info()
                    traceback.print_tb(tb)
                    raise

                logging.warning('sleeping %s minutes' % (stime/60))
                time.sleep(stime)

        return x

    return inner
