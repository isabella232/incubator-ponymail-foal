/*
 Licensed to the Apache Software Foundation (ASF) under one or more
 contributor license agreements.  See the NOTICE file distributed with
 this work for additional information regarding copyright ownership.
 The ASF licenses this file to You under the Apache License, Version 2.0
 (the "License"); you may not use this file except in compliance with
 the License.  You may obtain a copy of the License at

     http://www.apache.org/licenses/LICENSE-2.0

 Unless required by applicable law or agreed to in writing, software
 distributed under the License is distributed on an "AS IS" BASIS,
 WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 See the License for the specific language governing permissions and
 limitations under the License.
*/

async function sidebar_stats(json) {
    let obj = document.getElementById('sidebar_stats');
    if (!obj) {
        return;
    }

    obj.innerHTML = ""; // clear stats bar

    if (!json.emails || isHash(json.emails) || json.emails.length == 0) {
        obj.innerText = "No emails found...";
        return;
    }

    // Top 10 participants
    obj.inject("Found %u emails by %u authors, divided into %u topics.".format(json.emails.length, json.numparts, json.no_threads));
    obj.inject(new HTML('h5', {}, "Most active authors:"));
    for (var i = 0; i < json.participants.length; i++) {
        if (i >= 5) {
            break;
        }
        let par = json.participants[i];
        if (par.name.length > 24) {
            par.name = par.name.substr(0, 23) + "...";
        }
        if (par.name.length == 0) {
            par.name = par.email;
        }
        pdiv = new HTML('div', {
            class: "sidebar_stats_participant"
        });
        pimg = new HTML('img', {
            class: "gravatar_sm",
            src: "https://secure.gravatar.com/avatar/%s.jpg?s=64&r=g&d=mm".format(par.gravatar)
        })
        pdiv.inject(pimg);
        pdiv.inject(new HTML('b', {}, par.name + ": "));
        pdiv.inject(new HTML('br'));
        pdiv.inject("%u emails sent".format(par.count));
        obj.inject(pdiv);
    }

    // Word cloud, if applicable
    let wc = document.getElementById('sidebar_wordcloud');
    if (wc && json.cloud) {
        wc.innerHTML = "";
        wc.inject(new HTML('h5', {}, "Popular topics:"));
        // word cloud is delayed by 50ms to let the rest render first
        // this is a chrome-specific slowdown we're addressing.
        window.setTimeout(function() {
            wordCloud(json.cloud, 220, 100, wc);
        }, 50);
    }
}