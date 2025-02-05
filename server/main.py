#!/usr/bin/env python3
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

"""Apache Pony Mail, Codename Foal - A Python variant of Pony Mail"""
import argparse
import asyncio
import importlib
import json
import os
import sys
import traceback
import typing

import aiohttp.web
import yaml
import uuid

import plugins.background
import plugins.configuration
import plugins.database
import plugins.formdata
import plugins.offloader
import plugins.server
import plugins.session

PONYMAIL_FOAL_VERSION = "0.1.0"


# Certain environments such as MinGW-w64 will not register as a TTY and uses buffered output.
# In such cases, we need to force a flush of each print, or nothing will show.
if not sys.stdout.buffer.isatty():
    import functools
    print = functools.partial(print, flush=True)


class Server(plugins.server.BaseServer):
    """Main server class, responsible for handling requests and scheduling offloader threads """

    def __init__(self, args: argparse.Namespace):
        print(
            "==== Apache Pony Mail (Foal v/%s) starting... ====" % PONYMAIL_FOAL_VERSION
        )
        # Load configuration
        yml = yaml.safe_load(open(args.config))
        self.config = plugins.configuration.Configuration(yml)
        self.data = plugins.configuration.InterData()
        self.handlers = dict()
        self.dbpool = asyncio.Queue()
        self.runners = plugins.offloader.ExecutorPool()
        self.server = None
        self.streamlock = asyncio.Lock()

        # Make a pool of 15 database connections for async queries
        for _ in range(1, 15):
            self.dbpool.put_nowait(plugins.database.Database(self.config.database))

        # Load each URL endpoint
        for endpoint_file in os.listdir("endpoints"):
            if endpoint_file.endswith(".py"):
                endpoint = endpoint_file[:-3]
                m = importlib.import_module(f"endpoints.{endpoint}")
                if hasattr(m, "register"):
                    self.handlers[endpoint] = m.__getattribute__("register")(self)
                    print(f"Registered endpoint /api/{endpoint}")
                else:
                    print(
                        f"Could not find entry point 'register()' in {endpoint_file}, skipping!"
                    )
        if args.logger:
            import logging
            es_logger = logging.getLogger('elasticsearch')
            es_logger.setLevel(args.logger)
            es_logger.addHandler(logging.StreamHandler())
        if args.trace:
            import logging
            es_trace_logger = logging.getLogger('elasticsearch.trace')
            es_trace_logger.setLevel(args.trace)
            es_trace_logger.addHandler(logging.StreamHandler())

    async def handle_request(
        self, request: aiohttp.web.BaseRequest
    ) -> typing.Union[aiohttp.web.Response, aiohttp.web.StreamResponse]:
        """Generic handler for all incoming HTTP requests"""

        # Define response headers first...
        headers = {
            "Server": "Apache Pony Mail (Foal/%s)" % PONYMAIL_FOAL_VERSION,
        }

        # Figure out who is going to handle this request, if any
        # We are backwards compatible with the old Lua interface URLs
        body_type = "form"
        handler = request.path.split("/")[-1]
        if handler.endswith(".lua"):
            body_type = "form"
            handler = handler[:-4]
        if handler.endswith(".json"):
            body_type = "json"
            handler = handler[:-5]

        # Parse form data if any
        try:
            indata = await plugins.formdata.parse_formdata(body_type, request)
        except ValueError as e:
            return aiohttp.web.Response(headers=headers, status=400, text=str(e))

        # Find a handler, or 404
        if handler in self.handlers:
            session = await plugins.session.get_session(self, request)
            try:
                # Wait for endpoint response. This is typically JSON in case of success,
                # but could be an exception (that needs a traceback) OR
                # it could be a custom response, which we just pass along to the client.
                xhandler = self.handlers[handler]
                if isinstance(xhandler, plugins.server.StreamingEndpoint):
                    output = await xhandler.exec(self, request, session, indata)
                elif isinstance(xhandler, plugins.server.Endpoint):
                    output = await xhandler.exec(self, session, indata)
                if session.database:
                    self.dbpool.put_nowait(session.database)
                    self.dbpool.task_done()
                    session.database = None
                if isinstance(output, aiohttp.web.Response) or isinstance(output, aiohttp.web.StreamResponse):
                    return output
                if output:
                    jsout = await self.runners.run(json.dumps, output, indent=2)
                    headers["content-type"] = "application/json"
                    headers["Content-Length"] = str(len(jsout))
                    return aiohttp.web.Response(headers=headers, status=200, text=jsout)
                return aiohttp.web.Response(
                    headers=headers, status=404, text="Content not found"
                )
            # If a handler hit an exception, we need to print that exception somewhere,
            # either to the web client or stderr:
            except:
                if session.database:
                    self.dbpool.put_nowait(session.database)
                    self.dbpool.task_done()
                    session.database = None
                exc_type, exc_value, exc_traceback = sys.exc_info()
                err = "\n".join(
                    traceback.format_exception(exc_type, exc_value, exc_traceback)
                )
                # By default, we print the traceback to the user, for easy debugging.
                if self.config.ui.traceback:
                    return aiohttp.web.Response(
                        headers=headers, status=500, text="API error occurred: \n" + err
                    )
                # If client traceback is disabled, we print it to stderr instead, but leave an
                # error ID for the client to report back to the admin. Every line of the traceback
                # will have this error ID at the beginning of the line, for easy grepping.
                # We only need a short ID here, let's pick 18 chars.
                eid = str(uuid.uuid4())[:18]
                sys.stderr.write("API Endpoint %s got into trouble (%s): \n" % (request.path, eid))
                for line in err.split("\n"):
                    sys.stderr.write("%s: %s\n" % (eid, line))
                return aiohttp.web.Response(
                    headers=headers, status=500, text="API error occurred. The application journal will have "
                                                      "information. Error ID: %s" % eid
                )
        else:
            return aiohttp.web.Response(
                headers=headers, status=404, text="API Endpoint not found!"
            )

    async def server_loop(self, _loop: asyncio.AbstractEventLoop):  # Note, loop never used.
        self.server = aiohttp.web.Server(self.handle_request)
        runner = aiohttp.web.ServerRunner(self.server)
        await runner.setup()
        site = aiohttp.web.TCPSite(
            runner, self.config.server.ip, self.config.server.port
        )
        await site.start()
        print(
            "==== Serving up Pony goodness at %s:%s ===="
            % (self.config.server.ip, self.config.server.port)
        )
        await plugins.background.run_tasks(self)

    def run(self):
        loop = asyncio.get_event_loop()
        try:
            loop.run_until_complete(self.server_loop(loop))
        except KeyboardInterrupt:
            pass
        loop.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        help="Configuration file to load (default: ponymail.yaml)",
        default="ponymail.yaml",
    )
    parser.add_argument(
        "--logger",
        help="elasticsearch level (e.g. INFO or DEBUG)",
    )
    parser.add_argument(
        "--trace",
        help="elasticsearch.trace level (e.g. INFO or DEBUG)",
    )
    cliargs = parser.parse_args()
    pubsub_server = Server(cliargs)
    pubsub_server.run()
