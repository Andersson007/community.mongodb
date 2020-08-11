#!/usr/bin/python
# -*- coding: utf-8 -*-

# Copyright: (c) 2020, Andrew Klychkov (@Andersson007) <aaklychkov@mail.ru>
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import absolute_import, division, print_function
__metaclass__ = type


DOCUMENTATION = r'''
---
module: mongodb_document

short_description: Insert or get documents from MongoDB collection

description:
- Insert or get documents from MongoDB collection.

author: Andrew Klychkov (@Andersson007)
version_added: "1.1.0"

extends_documentation_fragment:
  - community.mongodb.login_options
  - community.mongodb.ssl_options

options:
  state:
    description:
    - Method to use.
    choises: [ find, find_one, insert_one, insert_many ]
    default: find_one

  collection:
    description:
    - Collection name to insert data to or to get data from.
    type: str
    required: yes

  db:
    description:
    - Database name where I(collection) exists.
    type: str
    required: yes

  data:
    description:
    - Data to pass to insert or find methods.
    - If I(state) is C(find_one) or C('insert_one'), it must be a dict.
    - If I(state) is C('insert_many'), it must be a list.
    type: list
    elements: dict

notes:
- Requires the pymongo Python package on the remote host, version 2.4.2+.

seealso:
- name: MongoDB python API reference
  description: Complete reference of PyMongo documentation.
  link: https://api.mongodb.com/python/current/tutorial.html

requirements: [ 'pymongo' ]
'''

EXAMPLES = r'''
- name: Find all documents from blogposts collection in myDB database where an author is Mike
  community.mongodb.mongodb_document:
    db: myDB
    collection: blogposts
    state: find
    data: { "author": "Mike" }

- name: Find one document from blogposts collection in myDB database where an author is Mike
  community.mongodb.mongodb_document:
    db: myDB
    collection: blogposts
    state: find
    data: { "author": "Mike" }

- name: Insert one document to blogposts collection im myDB
  community.mongodb.mongodb_document:
    db: myDB
    collection: blogposts
    state: insert_one
    data: { "author": "Mike", "title": "Mike's post" }

- name: Insert several documents to blogposts collection im myDB
  community.mongodb.mongodb_document:
    db: myDB
    collection: blogposts
    state: insert_many
    data:
    - { "author": "Mike", "title": "Mike's post" }
    - { "author": "Alice", "title": "Alice's post" }
    - { "author": "Bob", "title": "Bob's post" }
'''

RETURN = r'''
result:
  description: What a certain I(state) method returns.
  returned: always
  type: dict
  sample: {"author": "Mike", "title": "Mike's post"}
'''

from uuid import UUID

import ssl as ssl_lib
from distutils.version import LooseVersion

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils._text import to_native
from ansible.module_utils.six import iteritems
from ansible_collections.community.mongodb.plugins.module_utils.mongodb_common import (
    check_compatibility,
    missing_required_lib,
    mongodb_common_argument_spec,
    ssl_connection_options,
)
from ansible_collections.community.mongodb.plugins.module_utils.mongodb_common import (
    PyMongoVersion,
    PYMONGO_IMP_ERR,
    pymongo_found,
    MongoClient,
)


class MongoDbInfo():
    """Class for gathering MongoDB instance information.

    Args:
        module (AnsibleModule): Object of AnsibleModule class.
        client (pymongo): pymongo client object to interact with the database.
    """
    def __init__(self, module, client):
        self.module = module
        self.client = client
        self.admin_db = self.client.admin
        self.info = {
            'general': {},
            'databases': {},
            'total_size': {},
            'parameters': {},
            'users': {},
            'roles': {},
        }

    def get_info(self, filter_):
        """Get MongoDB instance information and return it based on filter_.

        Args:
            filter_ (list): List of collected subsets (e.g., general, users, etc.),
                when it is empty, return all available information.
        """
        self.__collect()

        inc_list = []
        exc_list = []

        if filter_:
            partial_info = {}

            for fi in filter_:
                if fi.lstrip('!') not in self.info:
                    self.module.warn("filter element '%s' is not allowable, ignored" % fi)
                    continue

                if fi[0] == '!':
                    exc_list.append(fi.lstrip('!'))

                else:
                    inc_list.append(fi)

            if inc_list:
                for i in self.info:
                    if i in inc_list:
                        partial_info[i] = self.info[i]

            else:
                for i in self.info:
                    if i not in exc_list:
                        partial_info[i] = self.info[i]

            return partial_info

        else:
            return self.info

    def __collect(self):
        """Collect information."""
        # Get general info:
        self.info['general'] = self.client.server_info()

        # Get parameters:
        self.info['parameters'] = self.get_parameters_info()

        # Gather info about databases and their total size:
        self.info['databases'], self.info['total_size'] = self.get_db_info()

        for dbname, val in iteritems(self.info['databases']):
            # Gather info about users for each database:
            self.info['users'].update(self.get_users_info(dbname))

            # Gather info about roles for each database:
            self.info['roles'].update(self.get_roles_info(dbname))

    def get_roles_info(self, dbname):
        """Gather information about roles.

        Args:
            dbname (str): Database name to get role info from.

        Returns a dictionary with role information.
        """
        db = self.client[dbname]
        result = db.command({'rolesInfo': 1, 'showBuiltinRoles': True})['roles']

        roles_dict = {}
        for elem in result:
            roles_dict[elem['role']] = {}
            for key, val in iteritems(elem):
                if key == 'role':
                    continue

                roles_dict[elem['role']][key] = val

        return roles_dict

    def get_users_info(self, dbname):
        """Gather information about users.

        Args:
            dbname (str): Database name to get user info from.

        Returns a dictionary with user information.
        """
        db = self.client[dbname]
        result = db.command({'usersInfo': 1})['users']

        users_dict = {}
        for elem in result:
            users_dict[elem['user']] = {}
            for key, val in iteritems(elem):
                if key == 'user':
                    continue

                if isinstance(val, UUID):
                    val = val.hex

                users_dict[elem['user']][key] = val

        return users_dict

    def get_db_info(self):
        """Gather information about databases.

        Returns a dictionary with database information.
        """
        result = self.admin_db.command({'listDatabases': 1})
        total_size = int(result['totalSize'])
        result = result['databases']

        db_dict = {}
        for elem in result:
            db_dict[elem['name']] = {}
            for key, val in iteritems(elem):
                if key == 'name':
                    continue

                if key == 'sizeOnDisk':
                    val = int(val)

                db_dict[elem['name']][key] = val

        return db_dict, total_size

    def get_parameters_info(self):
        """Gather parameters information.

        Returns a dictionary with parameters.
        """
        return self.admin_db.command({'getParameter': '*'})


def validate_data_type(module, state, data):
    if not isinstance(data, dict) or not isinstance(data, list):
        module.fail_json("Parameter 'data' must be a dict or list, passed %s" % type(data))

    if state == 'insert_many' and not isinstance(data, list):
        module.fail_json("Parameter 'data' must be a list "
                         "when state=insert_many, passed %s" % type(data))

# ================
# Module execution
#

def main():
    argument_spec = mongodb_common_argument_spec()
    argument_spec.update(
        state=dict(type='str', choises=['find', 'find_one', 'insert_one', 'insert_many']),
        db=dict(type='str', required=True),
        collection=dict(type='str', required=True),
        data=dict(type='raw'),
    )
    module = AnsibleModule(
        argument_spec=argument_spec,
        required_together=[['login_user', 'login_password']],
    )

    if not pymongo_found:
        module.fail_json(msg=missing_required_lib('pymongo'),
                         exception=PYMONGO_IMP_ERR)

    login_user = module.params['login_user']
    login_password = module.params['login_password']
    login_database = module.params['login_database']
    login_host = module.params['login_host']
    login_port = module.params['login_port']
    ssl = module.params['ssl']
    state = module.params['state']
    db = module.params['db']
    collection = module.params['collection']
    data = module.params['data']

    if data:
        validate_data_type(module, state, data)

    connection_params = {
        'host': login_host,
        'port': login_port,
    }

    if ssl:
        connection_params = ssl_connection_options(connection_params, module)

    client = MongoClient(**connection_params)

    if login_user:
        try:
            client.admin.authenticate(login_user, login_password, source=login_database)
        except Exception as e:
            module.fail_json(msg='Unable to authenticate: %s' % to_native(e))

    # Get server version:
    try:
        srv_version = LooseVersion(client.server_info()['version'])
    except Exception as e:
        module.fail_json(msg='Unable to get MongoDB server version: %s' % to_native(e))

    # Get driver version::
    driver_version = LooseVersion(PyMongoVersion)

    # Check driver and server version compatibility:
    check_compatibility(module, srv_version, driver_version)

    # Initialize an object and start main work:
    mongodb = MongoDbCollection(module, db, collection)

    module.exit_json(changed=False, **mongodb.execute(state, data))


if __name__ == '__main__':
    main()
