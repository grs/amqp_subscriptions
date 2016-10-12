#!/usr/bin/env python
#
# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
#

from __future__ import print_function
import optparse
from proton import Url
from proton.handlers import MessagingHandler
from proton.reactor import Container, DurableSubscription
from proton_extension import Capabilities

class Recv(MessagingHandler):
    def __init__(self, url, id, subscription, count):
        super(Recv, self).__init__()
        self.url = Url(url)
        self.id = id
        self.subscription = subscription
        self.expected = count
        self.received = 0

    def on_start(self, event):
        # non-shared, durable with global subscription identifier
        if self.id:
            event.container.container_id = self.id
        event.container.create_receiver(self.url, name=self.subscription, options=[DurableSubscription(), Capabilities('global')])

    def on_message(self, event):
        if self.expected == 0 or self.received < self.expected:
            print(event.message.body)
            self.received += 1
            if self.received == self.expected:
                event.receiver.close()
                event.connection.close()

parser = optparse.OptionParser(usage="usage: %prog [options]")
parser.add_option("-a", "--address", default="localhost:5672/examples",
                  help="address from which messages are received (default %default)")
parser.add_option("-m", "--messages", type="int", default=100,
                  help="number of messages to receive; 0 receives indefinitely (default %default)")
parser.add_option("-i", "--id", default=None,
                  help="client's connection identifier (default %default)")
parser.add_option("-s", "--subscription", default="subscription-c",
                  help="client's subscription identifier (default %default)")
opts, args = parser.parse_args()

try:
    Container(Recv(opts.address, opts.id, opts.subscription, opts.messages)).run()
except KeyboardInterrupt: pass



