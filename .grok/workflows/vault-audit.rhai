let meta = #{
    name: "vault-audit",
    description: "Read-only vault health + wiki-graph + volatile-lesson pass, then a structured report",
    when_to_use: "When the operator wants a Scout-shaped audit without dispatching Scout, or after a path/venv incident",
    phases: [
        #{ title: "Probe", detail: "doctor, graph, inbox in parallel" },
        #{ title: "Synthesize", detail: "one report from the three probes" },
    ],
};

let probe_schema = #{
    "type": "object", "required": ["summary", "findings"],
    "properties": #{
        "summary": #{ "type": "string" },
        "findings": #{ "type": "array", "maxItems": 12, "items": #{
            "type": "object", "required": ["id", "severity", "detail"],
            "properties": #{
                "id": #{ "type": "string" },
                "severity": #{ "type": "string" },
                "detail": #{ "type": "string" },
            },
        }},
    },
};

let root = if args == () { () } else { args.root };
if root == () { root = "."; }

phase("Probe");
log("parallel doctor / graph / inbox");

let jobs = [];
jobs.push(#{
    prompt: "Run read-only vault health. You MUST execute: python3 " + root + "/bin/vault-doctor --root " + root + " --json. Then also run: python3 " + root + "/bin/vault-doctor --root " + root + " --session-start. Paste both outputs. Summarize every check that is not ok as a finding {id, severity, detail}. Empty findings is valid only after you actually ran the commands.",
    label: "doctor",
    capability_mode: "execute",
    output_schema: probe_schema,
});
jobs.push(#{
    prompt: "Run the wiki graph tool. You MUST execute: python3 " + root + "/bin/vault-graph --root " + root + " --json. From the JSON, report: page count, broken-link count, orphan count, collision count, top 5 hubs, and up to 8 concrete findings {id, severity, detail} for broken links or collisions that would mislead recall. Do not invent pages — only quote ids from the JSON. A missing command output is a failed probe, not an empty vault.",
    label: "graph",
    capability_mode: "execute",
    output_schema: probe_schema,
});
jobs.push(#{
    prompt: "Inspect vault inboxes without modifying them. List files in " + root + "/vault-personal/inbox/ (not _processed) and " + root + "/vault-company/inbox/. For each file report name + mtime + first 8 lines. Also read " + root + "/vault-personal/_maintenance/phone-channel-stamp.json if it exists. Findings: any file older than 24h is high; leftover .claimed-* is high; company files sitting for weeks are medium. Use list_dir and read_file. Empty findings only after you listed both inboxes.",
    label: "inbox",
    capability_mode: "read-only",
    output_schema: probe_schema,
});

let results = parallel(jobs);

phase("Synthesize");
let blobs = [];
let i = 0;
for r in results {
    if r != () && r.success && r.output.summary != () {
        blobs.push(json_encode(r.output));
    } else {
        blobs.push("probe-failed:" + i.to_string());
    }
    i += 1;
}

let syn = agent(
    "You are writing a one-screen vault audit for the operator. Inputs are JSON blobs from three independent probes (doctor, graph, inbox). Merge them. Lead with the single highest-severity fact. Then a short table of findings. Then at most three next actions, each one command or one dispatch. Do not recommend rebuilding the venv if relink would fix it. Do not recommend PageIndex or new architecture. Write the report in markdown."
        + "\n\nPROBE 0 (doctor): " + blobs[0]
        + "\n\nPROBE 1 (graph): " + blobs[1]
        + "\n\nPROBE 2 (inbox): " + blobs[2],
    #{ label: "synthesize", capability_mode: "read-only" }
);

let report = if syn.success { syn.output } else { "synthesis failed" };
let path = write_scratch_file("vault-audit.md", report);
complete(#{ path: path, report: report });
