#!/usr/bin/env python
#
# This file is part of Ansible
#
# Ansible is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Ansible is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Ansible. If not, see <http://www.gnu.org/licenses/>.

from __future__ import print_function

import logging
import sys

from ansibullbot.triagers.ansible import AnsibleTriage
from ansibullbot.utils.sentry import initialize_sentry


def handle_exception(exc_type, exc_value, exc_traceback):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    logging.error("Uncaught exception", exc_info=(exc_type, exc_value, exc_traceback))


sys.excepthook = handle_exception


def main():
    initialize_sentry()

    # Set up logging
    log_level = logging.DEBUG
    log_format = '%(asctime)s %(levelname)s %(filename)s:%(lineno)d %(message)s'
    
    # Configure root logger
    logging.basicConfig(
        level=log_level,
        format=log_format,
        handlers=[
            logging.StreamHandler()
        ]
    )

    # Disable unwanted loggers
    logging.getLogger('urllib3').setLevel(logging.WARNING)
    logging.getLogger('requests').setLevel(logging.WARNING)
    logging.getLogger('github').setLevel(logging.WARNING)
    
    # Create triager with disabled receiver
    args = sys.argv[1:]
    if '--verbose' in args:
        args.remove('--verbose')  # Remove unsupported arg
        
    # Add default args to avoid receiver dependency
    default_args = [
        '--skip_no_update',
        '--force',
        '--ignore_galaxy'
    ]
    args.extend(default_args)
    
    triager = AnsibleTriage(args=args)
    triager.start()


if __name__ == "__main__":
    main()
