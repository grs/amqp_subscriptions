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

import collections, optparse
from proton import Condition, Endpoint, generate_uuid, Terminus
from proton.handlers import MessagingHandler
from proton.reactor import Container

def extract_object(d):
    d.rewind()
    d.next()
    obj = d.get_object()
    d.rewind()
    return obj

def has_capability(link, capability):
    capabilities = extract_object(link.remote_source.capabilities)
    return (isinstance(capabilities, list) and capability in capabilities) or capabilities == capability

def is_global(link):
    return has_capability(link, u'global')

def is_shared(link):
    return has_capability(link, u'shared')

def is_durable(link):
    return link.remote_source.durability == Terminus.DELIVERIES

def get_unqualified(name):
    i = name.find("|")
    if i > 0:
        return name[:i]
    else:
        return name

def get_id(link):
    if is_global(link):
        return get_unqualified(link.name)
    else:
        return "%s:%s" % (get_unqualified(link.name), link.connection.remote_container)

class Subscription(object):
    def __init__(self, shared, durable):
        self.shared = shared
        self.durable = durable
        self.queue = collections.deque()
        self.consumers = []

    def in_use(self):
        return len(self.consumers) > 0

    def attach(self, consumer):
        self.consumers.append(consumer)

    def detach(self, consumer, closed):
        if consumer in self.consumers:
            self.consumers.remove(consumer)
        return len(self.consumers) == 0 and (closed or not self.durable)

    def publish(self, message):
        self.queue.append(message)
        self.dispatch()

    def _rotate(self):
        self.consumers = self.consumers[1:] + self.consumers[:1]
        return self.consumers

    def dispatch(self, consumer=None):
        if consumer:
            c = [consumer]
        else:
            c = self._rotate()
        while self._deliver_to(c): pass

    def _deliver_to(self, consumers):
        try:
            result = False
            for c in consumers:
                if c.credit:
                    c.send(self.queue.popleft())
                    result = True
            return result
        except IndexError: # no more messages
            return False


class Topic(object):
    def __init__(self, address):
        self.subscriptions = {}
        self.address = address

    def attach(self, link):
        name = get_id(link)
        s = self.subscriptions.get(name)
        if not s:
            print("creating subscription for %s on %s" % (name, self.address))
            s = Subscription(is_shared(link), is_durable(link))
            self.subscriptions[name] = s
        else:
            print("attaching to existing subscription for %s on %s" % (name, self.address))
            if s.in_use() and not is_shared(link):
                raise Exception("Subscription %s already in use!" % name)
            if s.shared != is_shared(link):
                if s.shared:
                    raise Exception("Must request shared capability to share subscription!")
                else:
                    raise Exception("Existing subscription is not shared!")
            if s.durable != is_durable(link):
                if s.durable:
                    raise Exception("Existing subscription is durable!")
                else:
                    raise Exception("Existing subscription is not durable!")
        s.attach(link)

    def detach(self, link, closed):
        name = get_id(link)
        s = self.subscriptions.get(name)
        if s:
            print("detaching subscription %s on %s" % (name, self.address))
            if s.detach(link, closed):
                del self.subscriptions[name]
        return not self.subscriptions

    def publish(self, message):
        for s in self.subscriptions:
            self.subscriptions[s].publish(message)

    def dispatch(self, link):
        name = get_id(link)
        s = self.subscriptions.get(name)
        if s:
            s.dispatch(link)

class Broker(MessagingHandler):
    def __init__(self, url):
        super(Broker, self).__init__()
        self.url = url
        self.topics = {}

    def on_start(self, event):
        self.acceptor = event.container.listen(self.url)

    def _topic(self, address):
        if address not in self.topics:
            print("creating topic %s" % address)
            self.topics[address] = Topic(address)
        return self.topics[address]

    def on_link_remote_open(self, event):
        if event.link.is_sender:
            try:
                self._topic(event.link.remote_source.address).attach(event.link)
                event.link.source.address = event.link.remote_source.address
            except Exception as e:
                print("Attach failed: %s" % e)
                event.link.condition = Condition('amqp:precondition-failed', "%s" % e)
                event.link.close()
        elif event.link.remote_target.address:
            event.link.target.address = event.link.remote_target.address

    def _unsubscribe(self, link, closed):
        if link.source.address in self.topics and self.topics[link.source.address].detach(link, closed):
            print("deleting topic %s" % link.source.address)
            del self.topics[link.source.address]

    def on_link_remote_close(self, event):
        if event.link.is_sender:
            self._unsubscribe(event.link, True)

    def on_link_remote_detach(self, event):
        if event.link.is_sender:
            self._unsubscribe(event.link, False)

    def on_connection_closing(self, event):
        self.remove_stale_consumers(event.connection)

    def on_disconnected(self, event):
        self.remove_stale_consumers(event.connection)

    def remove_stale_consumers(self, connection):
        l = connection.link_head(Endpoint.REMOTE_ACTIVE)
        while l:
            if l.is_sender:
                self._unsubscribe(l, False)
            l = l.next(Endpoint.REMOTE_ACTIVE)

    def on_sendable(self, event):
        if event.link.source.address in self.topics:
            self.topics[event.link.source.address].dispatch(event.link)

    def on_message(self, event):
        if event.link.target is None:
            address = event.message.address
        else:
            address = event.link.target.address
        if address in self.topics:
            self.topics[address].publish(event.message)

parser = optparse.OptionParser(usage="usage: %prog [options]")
parser.add_option("-a", "--address", default="localhost:5672",
                  help="address router listens on (default %default)")
opts, args = parser.parse_args()

try:
    Container(Broker(opts.address)).run()
except KeyboardInterrupt: pass
