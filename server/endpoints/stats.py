#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Licensed to the Apache Software Foundation (ASF) under one or more
# contributor license agreements.  See the NOTICE file distributed with
# this work for additional information regarding copyright ownership.
# The ASF licenses this file to You under the Apache License, Version 2.0
# (the "License"); you may not use this file except in compliance with
# the License.  You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Simple endpoint that returns the server's gathered activity data"""
""" THIS ONLY DEALS WITH PUBLIC EMAILS FOR NOW - AAA IS BEING WORKED ON"""
import plugins.server
import plugins.session
import plugins.messages
import plugins.defuzzer
import plugins.offloader
import re
import email.utils
import typing
import aiohttp.web
import time

PYPONY_RE_PREFIX = re.compile(r"^([a-zA-Z]+:\s*)+")


async def process(
    server: plugins.server.BaseServer, session: plugins.session.SessionObject, indata: dict,
) -> typing.Union[dict, aiohttp.web.Response]:

    # must provide list and domain
    xlist = indata.get("list", None)
    xdomain = indata.get("domain", None)
    if not xlist or not xdomain:
        return aiohttp.web.Response(headers={"content-type": "application/json",}, text='{}')

    try:
        query_defuzzed = plugins.defuzzer.defuzz(indata)
        query_defuzzed_nodate = plugins.defuzzer.defuzz(indata, nodate=True)
    except AssertionError as e:  # If defuzzer encounters syntax errors, it will throw an AssertionError
        return aiohttp.web.Response(headers={"content-type": "text/plain",}, status=500, text=str(e))
    results = await plugins.messages.query(
        session, query_defuzzed, query_limit=server.config.database.max_hits, shorten=True,
    )

    # statsOnly: Whether to only send statistical info (for n-grams etc), and not the
    # thread struct and message bodies
    # Param: quick
    statsOnly = 'quick' in indata
    # emailsOnly: return email summaries only, not derived data:
    # i.e. omit thread_struct, top 10 participants and word-cloud   
    emailsOnly = 'emailsOnly' in indata

    wordcloud = None
    if server.config.ui.wordcloud and not emailsOnly and not statsOnly:
        wordcloud = await plugins.messages.wordcloud(session, query_defuzzed)
    oldest, youngest, active_months = await plugins.messages.get_activity_span(session, query_defuzzed_nodate)

    authors = {}
    tstruct = {}
    top10_authors = None
    if not statsOnly and not emailsOnly:
        threads = plugins.messages.ThreadConstructor(results)
        tstruct, authors = await server.runners.run(threads.construct)

        all_authors = sorted(authors.items(), key=lambda x: x[1], reverse=True)  # sort in reverse by author count
        top10_authors = []
        for author, count in all_authors[:10]:
            name, address = email.utils.parseaddr(author)
            top10_authors.append(
                {"email": address, "name": name, "count": count, "gravatar": plugins.messages.gravatar(author),}
            )

    # Trim email data so as to reduce download sizes
    for msg in results:
        if statsOnly:
            for header in list(msg.keys()):
                if not header == 'epoch':
                    del msg[header]
        else:
            plugins.messages.trim_email(msg, external=True)

    output = {
        "firstYear": oldest.year,
        "lastYear": youngest.year,
        "firstMonth": oldest.month,
        "lastMonth": youngest.month,
        "active_months": active_months,
        "hits": len(results),
        "numparts": len(authors),
        "no_threads": len(tstruct),
        "emails": list(sorted(results, key=lambda x: x["epoch"])),
        "participants": top10_authors or {},
        "searchlist": f"<{xlist}.{xdomain}>",
        "domain": xdomain,
        "name": xlist,
        "list": f"{xlist}@{xdomain}",
        "searchParams": indata,
        "unixtime": int(time.time()),
    }
    if not statsOnly and not emailsOnly:
        output['thread_struct'] = tstruct
    if wordcloud:
        output['cloud'] = wordcloud
    return output


def register(server: plugins.server.BaseServer):
    return plugins.server.Endpoint(process)
