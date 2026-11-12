# arcadecheck

Inventory every Arcade expression you own and say which ones can leave with you.

A team decides to move a layer off ArcGIS Online. The first question is how much Arcade is
attached to it, and nobody can answer. The expressions are not in one place: some are in popups,
some in labels, some in a renderer, some in the form that field crews use, some in a dashboard
that reads the same service, and some are attribute rules inside the geodatabase the service was
published from. Each one lives in a different editor, and none of those editors lists the others.

So the migration estimate is a guess. It is wrong in both directions. Half of the expressions are
arithmetic over the row's own fields and will move to anything, and a one line expression that
nobody thought twice about calls `FeatureSetByName` and cannot leave ArcGIS at all. You find out
in the third week, when the layer is already half moved.

`arcadecheck` reads the documents you already have, finds every Arcade expression in them, and
rates each one PORTABLE, PORTABLE WITH WORK or ESRI ONLY with the reason. It writes a CSV. It
does not convert anything.

The tools people use today are the ArcGIS Online Assistant and ArcGIS Pro's Attribute Rules view.
For one item they are the right tool. The Assistant shows an item's raw JSON, edits it in place
and needs nothing installed. The Attribute Rules view lists every rule on a feature class with its
expression. Both answer "what is in this thing". Neither spans popups, labels, renderers,
dashboards, forms and attribute rules at once, and neither reads an expression and tells you it
calls `FeatureSetByName`. That is the gap this tool fills. It does not replace them: use them to
edit, and use this to size the job.

```
$ python arcadecheck.py --self-test
arcadecheck self-test: no portal, no geodatabase, no credentials
--------------------------------------------------------------------
PASS  a line comment is blanked out
PASS  and the blanked text is the same length as what came in
PASS  the newline that ends a line comment survives it
PASS  a block comment is blanked out
PASS  a block comment keeps the lines it spans, so line numbers still line up
PASS  a double quoted literal is blanked out
PASS  a single quoted literal is blanked out too
PASS  an escaped quote does not end the literal early
PASS  an unterminated literal swallows the rest, which is what Arcade does
PASS  an unterminated block comment swallows the rest for the same reason
PASS  an empty or null expression strips to the empty string
PASS  a division is not the start of a comment  <-- pinned defect
...
PASS  the self-test leaves no temporary directory behind  <-- pinned defect
PASS  a console that cannot carry an accent gets it escaped rather than a traceback  <-- pinned defect
PASS  and the escape says which character it was
PASS  the harness records a false check, a missing exception and a wrong exception as three failures, so a broken tool turns this self-test red  <-- pinned defect
PASS  and a failure is printed as FAIL, so the transcript shows which assertion went red
PASS  a failed assertion is listed by its label under a count that includes it, so a red run says what went red
PASS  and the exit status is 1 only when something failed
PASS  loading the file under another name defines main and does not run it
--------------------------------------------------------------------
642 assertions, 0 failed
```

The first 14 lines and the last 10 lines of one real run, which is 646 lines long.

## Requirements

Python 3.9 or newer. Standard library only: `argparse`, `csv`, `http.client`, `io`, `json`, `os`, `re`, `ssl`,
`sys` and `urllib`, with `http.server`, `runpy`, `threading`, `tempfile`, `shutil` and `time` used inside the
self-test. It runs on ArcGIS Pro's Python and on a plain `python3`. `arcpy` is not used and the
`arcgis` package is not needed.

```
git clone https://github.com/uhsear/arcadecheck.git
python arcadecheck.py --self-test
```

`--self-test` needs no portal, no credentials and no geodatabase. It covers the rater, the four
collectors, the CSV and the command line, and it reads one item through the same `urllib` code
path a real portal gets, from a stand-in server on the loopback address.

## Quick start

Point it at a web map you saved out of the item's JSON:

```
python arcadecheck.py webmap.json
```

Attribute rules come from `gdbxray`, which reads the geodatabase so this tool does not have to:

```
python gdbxray.py water.gdb --json --out rules.json --apply
python arcadecheck.py rules.json
```

```
arcadecheck: 6 expression(s) in 1 document(s)
--------------------------------------------------------------------
ESRI ONLY          rules.json / rule / CountHydrants
    layer: WaterLines
    $datastore the workspace the edited feature came from
    FeatureSetByName reads another layer by name out of the map or the geodatabase
ESRI ONLY          rules.json / rule / OwnerName
    layer: WaterLines
    $datastore the workspace the edited feature came from
    FeatureSetByName reads another layer by name out of the map or the geodatabase
PORTABLE WITH WORK rules.json / rule / SnapCheck
    layer: Hydrants
    Buffer a geometry operation; shapely and GEOS do the same work under another name
    Geometry a geometry operation; shapely and GEOS do the same work under another name
    Intersects a geometry operation; shapely and GEOS do the same work under another name
--------------------------------------------------------------------
  ESRI ONLY            2
  PORTABLE WITH WORK   1
  PORTABLE             3
  by location:         rule 6
  what blocks a move:
      2  $datastore                   the workspace the edited feature came from
      2  FeatureSetByName             reads another layer by name out of the map or the geodatabase
      1  Buffer                       a geometry operation; shapely and GEOS do the same work under another name
      1  Geometry                     a geometry operation; shapely and GEOS do the same work under another name
      1  Intersects                   a geometry operation; shapely and GEOS do the same work under another name
```

That run is a real one, against a synthetic File Geodatabase built for the self-test. `OwnerName` is one
line long and it is still ESRI ONLY.

## Usage

Documents on disk, a portal, or both in one run. Nothing is written without `--apply`.

```
python arcadecheck.py webmap.json dashboard.json form.json rules.json
python arcadecheck.py webmap.json --out arcade.csv --apply
python arcadecheck.py --portal https://example.maps.arcgis.com --item 1a2b3c --item 4d5e6f
```

| Flag | Default | What it does |
|---|---|---|
| `FILE` | none | Web map, dashboard, form or `gdbxray --json` documents. Any number of them. |
| `--portal` | none | Portal to read items from. Must start with `https://` or `http://`. |
| `--item` | none | Item id to read from `--portal`. Repeatable. |
| `--token` | none | Portal token. A token on the command line is visible in the process list and in shell history, so this flag prints a warning. Prefer `--token-stdin` or the env var `ARCADECHECK_TOKEN`. |
| `--token-stdin` | off | Read the token from the first line of standard input. It cannot be combined with `--token`. |
| `--insecure` | off | Skip TLS verification, for an Enterprise portal behind an internal CA. |
| `--all` | off | List the portable expressions too, not only the ones that need work. |
| `--out` | none | CSV file to write the inventory to. |
| `--apply` | off | Write the `--out` file. Without it nothing is written. |
| `--self-test` | off | Run the offline assertions and exit. |

The CSV has one row per expression: `source`, `document`, `where`, `layer`, `name`, `rating`,
`reasons`, `calls`, `chars`, `lines`, `path` and the `expression` itself. `path` is the JSON path
the expression was read from, so every row can be found again in the document it came from.

Every text cell is guarded against spreadsheet formula injection. A cell that starts with `=`, `+`,
`-`, `@`, a tab or a carriage return gets one apostrophe in front, so Excel reads it as text and
does not run it. The text comes from documents and portals you did not write: a layer title of
`=HYPERLINK(...)` is legal, and an Arcade expression that starts with a minus sign is ordinary.
`chars` and `lines` are numbers and are never touched. To get the original text back, drop exactly
one leading apostrophe. This tool does not read its own CSV back, so it has no reader to change.

## What it checks (or refuses)

It collects Arcade from five places:

- **Popups.** `popupInfo.expressionInfos[]` on an operational layer or a table.
- **Labels.** `labelExpressionInfo.expression`, which sits under `drawingInfo` beside the
  renderer. The legacy `labelExpression`, the `[FIELDNAME]` syntax, is **not** collected. It was
  never Arcade, and counting it would inflate the estimate.
- **Renderers.** `valueExpression` on a renderer and on each of its visual variables.
- **Forms.** `formInfo.expressionInfos[]`, the calculation and constraint expressions. A form
  element that points at one of those by **name** is not a second expression, and is skipped.
- **Dashboards.** Every `expression` key in a document that has a `widgets` list.
- **Attribute rules.** From `gdbxray --json` output, not from the geodatabase. `gdbxray` already
  reads `GDB_Items`, and there is no reason for a second tool to open a `.gdb`.

Each expression is rated on **what it calls**:

| Rating | Means | Examples of what earns it |
|---|---|---|
| `ESRI ONLY` | There is no equivalent outside ArcGIS. | `FeatureSetByName`, `FeatureSetByPortalItem`, `FeatureSetByRelationshipName`, `FeatureSetByRelationshipClass`, `KnowledgeGraphByPortalItem`, `Portal()`, `GetUser`, `$map`, `$datastore`, `$layer`, `$editcontext`, `$originalfeature`, `$view`, `$aggregatedfeatures`, `GetFeatureSet`, `GetFeatureSetInfo`, `GdbVersion` |
| `PORTABLE WITH WORK` | Something else does this, under another name. | geometry predicates and operations (shapely and GEOS), geodesic measurement (pyproj or geographiclib), domain and subtype lookups including `FilterBySubtypeCode` (rebuild as a table), attachments |
| `PORTABLE` | It moves as it is. | arithmetic and text over the row's own fields, `IIf`, `When`, `Count`, `Filter`, date formatting |

The full list of names in each rating is at the top of the file, next to the reason printed for
each one. Adding to it is the intended way to extend the tool.

One guard is on the output side. The CSV is the only thing this tool writes, and it is written
only with `--apply`. Every text column goes through `guard_cell` first, when the value is a string (see Limits for
the one case where it is not), whatever put the text
there: the document, the portal, the file name you passed, or a key in the JSON path. The
six characters are the ones this tool guards. OWASP's page lists those six and also a line feed
and the full-width forms of `=`, `+`, `-` and `@`, which are not guarded here. The single quote
prefix is one of the mitigations the page names; for Excel it prefers a tab, and it warns that
none of them is reliable after the file is saved and reopened. Treat the guard as a first line and
not as a guarantee. A cell that starts with a space or a line feed is not guarded.

Four things it refuses to be fooled by:

- **Length is not portability.** Nothing in the rater can see how long an expression is. A one
  line `FeatureSetByName($datastore, "Hydrants")` is ESRI ONLY. A thirty line block of arithmetic
  is PORTABLE. The self-test pins both, on a real geodatabase's rules as well as on made up ones.
- **A name inside a comment or a string is not a call.** Comments and string literals are blanked
  out before anything is searched for, so `// FeatureSetByName was removed here` rates PORTABLE.
  Without that, a migration note in a comment costs somebody a week. A template literal, the
  text between backticks that Arcade has had since version 1.11, is blanked too, except for the
  code inside its `${ }`. An apostrophe in `${$feature.OWNER}'s parcel` therefore opens no
  string, and a call written after it is still found.
- **A name after a dot is not a call.** `$feature.Buffer(3)` reads a field called `Buffer`. The
  geometry function of that name is `Buffer($feature, ...)`, at the start of the expression.
- **An expression that does not parse is never clean.** A string, block comment or template that
  is never closed leaves the blanking unable to tell code from text. Such an expression is rated
  at least PORTABLE WITH WORK, with the reason `(unclosed)`, and its calls are read from the raw
  text, so a `FeatureSetByName` that a stray quote would have hidden still makes it ESRI ONLY.
  The cost is that a name in a comment of a broken expression also counts.

The credential rules are the same ones the rest of this portfolio uses. There is no `--password`
flag, because `argv` is readable by every process on the machine. The token can come from the
first line of standard input with `--token-stdin`, or from `ARCADECHECK_TOKEN`, instead of the
command line. `--token` still works and prints a warning. A mistyped flag that carries a value in
one word, such as `--tokn=SECRET`, is refused with the value replaced by `[redacted]`, and a glued one with no `=`, such as `--tokenSECRET`, is refused with the whole argument replaced by `[redacted]`. A portal url with no scheme, a space or a control
character in it, a user name or password in it (`https://user:pw@host`), and an item id with a
trailing newline, are refused before a request is built,
because `urllib` and `http.client` quote the whole url into their own exceptions and that url
carries the token. A token that a portal echoes back inside an error message is masked before the
message is printed, both as typed and as sent (percent encoded). A truncated answer, a read
timeout and a reply that is not HTTP exit 2 with a message that has no token in it.

## Exit codes

| Code | Means |
|---|---|
| 0 | Every expression found is PORTABLE, in documents the tool recognised. A web map, dashboard, form or gdbxray report with no Arcade in it counts. |
| 1 | At least one expression is PORTABLE WITH WORK or ESRI ONLY. |
| 2 | A document could not be read, a portal call failed, the CSV could not be written, or the tool could not tell: a file of no known shape that yields nothing, a saved portal error, or a gdbxray report whose rule count is not an integer, does not match the rules read, or whose rules hold no text under `expression`. The file is named on stderr. |
| 64 | Usage error: no input, an `--item` with no `--portal`, a portal url with no scheme or with a user name or password in it, or `--token-stdin` with nothing on standard input. |

Exit 1 is the normal answer for a real organization. It means there is work, not that the run
failed.

## Limits

- **The deliverable is a CSV, not a conversion.** It tells you the size of the job. It does not
  do the job, and it will not rewrite one line of Arcade for you.
- It reads the documents you give it. It does not crawl an organization, and `--item` takes ids
  rather than a search. Use `itemcensus` to produce the list of ids first.
- Attribute rules are read from `gdbxray --json` output. This tool never opens a geodatabase, so
  a rule that is in the `.gdb` and not in that report is not in the CSV.
- The collector matches JSON **key names** known to hold Arcade: `expression`, `valueExpression`
  and `scriptExpression`. A product that invents a new key holds expressions this tool will not
  find until that key is added to the list at the top of the file. The `path` column is there so
  you can check a document by hand and see what was picked up.
- A rating is about the functions an expression calls, not about whether the data behind it
  exists. A PORTABLE expression over a field that only lives in the geodatabase still needs that
  field.
- `FeatureSetByName` in an attribute rule usually names a **GUID**, not a layer. ArcGIS rewrites
  the name you typed into the destination class's UUID when the rule is saved, so the expression
  cannot be read without the geodatabase it came from. The tool reports the call; it cannot tell
  you which class the GUID is.
- Nothing is executed. An expression that would fail at runtime rates the same as one that works.
- The CSV is written as UTF-8 with a byte order mark, so Excel opens accented text correctly.
  A reader that does not expect the mark needs `encoding="utf-8-sig"`.
- A guarded cell carries a leading apostrophe on disk, and a script that reads the CSV sees it.
  The tool cannot tell that apostrophe from one already in the text, when the original started
  with an apostrophe and then a formula character: such a cell is written as it came. A reader
  that must be exact should treat a leading apostrophe followed by one of `=`, `+`, `-`, `@`, tab
  or carriage return as the guard.
- The guard stops a spreadsheet from running a cell on open. OWASP says the prefix can be lost
  when Excel saves the file and it is opened again.
- The ratings for `$map`, `$datastore`, `$layer`, `$editcontext` and `$originalfeature` are
  confirmed against the profile pages named under Verification. The others rest on earlier work. `$aggregatedfeatures` is named on none of those pages, so its
  ESRI ONLY rating is not confirmed. `$view` is ESRI ONLY: the popup page lists it as a
  dictionary that is "only supported in 2D MapViews", so `$view.scale` does not move as it is.
  `$camera` and `$sensor` are named on none of those pages and are not in the ESRI ONLY list, so
  an expression that reads one of them rates PORTABLE. So does `$userInput`. `$graph` is ESRI ONLY: the popup
  profile lists it as type KnowledgeGraph, "A knowledge graph associated with the feature".
- The rater finds calls by pattern and does not parse Arcade. It cannot tell whether an
  expression is valid, so a string or comment that is never closed makes the rating a conservative
  one, not a certain one: see the fourth item under "Four things it refuses to be fooled by". A
  space between a dot and a name, as in
  `$feature . Buffer(3)`, is read as a geometry call and rates PORTABLE WITH WORK. Arcade's page
  on template literals does not say how a backtick is escaped, so an escaped backtick in one is read the way
  JavaScript reads it.
- The guard covers `=`, `+`, `-`, `@`, tab and carriage return. A cell that starts with a line
  feed or a full-width `=`, `+`, `-` or `@` is written as it came, although OWASP lists those too.
- A dashboard is recognised only when `widgets` is a top-level list. If a real Dashboards item nests its widgets, for example under a view key, it is read as generic JSON: every `expression` key is still collected, but the `where` column falls back to `expression` instead of `dashboard`.
- The one assertion removed since the first release, "and argparse says which flag it was", was dropped on purpose so that a mistyped token flag is no longer echoed. The lost detail is the one the redaction bullets above describe.
- Windows PowerShell 5 writes `>` redirects as UTF-16. The tool refuses such a file with exit code 2 and "not UTF-8 text"; save the JSON as UTF-8 first.
- The CSV holds the full text of every expression, including any key, token or password written
  into one. This tool does not scan for secrets. Treat the file as sensitive and read it before
  you share it. The report on screen does not print expression text.
- A token passed with `--token` is visible in the process list and in shell history. Use
  `--token-stdin` or `ARCADECHECK_TOKEN`. The token is sent in the query string of the request
  (`f=json&token=...`), so it also lands in the access logs of the portal, its web adaptor and any
  proxy on the way. Esri's REST pages describe a token in a request header as well; this tool
  does not use that.
- Use an `https://` portal. With `http://` the token travels unencrypted in the query string.
- `--insecure` turns certificate checks off without a warning, and the token is still sent. The
  self-test checks only that the SSL context has verification off, because the portal stub speaks
  plain `http` and the standard library cannot make a certificate. A TLS handshake, with and
  without `--insecure`, was run by hand against a local self-signed server and not in the
  self-test.
- A word typed after a switch that takes no value, such as a token after `--token-stdin`,
  `--insecure` or `--all`, is read as a file name when no FILE comes before it. If no such file exists the error says "could
  not read [redacted]" and does not print the word, so a mistyped file name is hidden as well.
  A word that names a file that does exist is printed. When a FILE comes first, the word is left over after the switch and the usage error shows it as `[redacted]`; every leftover word is hidden that way. `--token-stdin` also waits for input
  when run from a terminal, even if only files are given.
- A non-numeric port in `--portal` is quoted back in the error message. The token itself is never
  in that text.
- A redirect to an `ftp://` address is followed as well as `http` and `https` redirects, and the
  error message for a failed one has no detail.
- The token from `ARCADECHECK_TOKEN` is used exactly as set, with no trimming, so a trailing
  newline or space in it makes the portal refuse it.
- The flag `--ap` is refused with exit code 2 and nothing written, but the message says
  "unrecognized arguments: [redacted]", so it does not name the flag that was mistyped.
- The 16 Track functions (`TrackCurrentSpeed`, `TrackWindow` and the rest) rate ESRI ONLY. Esri's
  [Track functions](https://developers.arcgis.com/arcade/function-reference/track_functions/)
  page says they "are only available when using GeoAnalytics within specific track-enabled
  tools". `QueryGraph` (listed under Knowledge graph functions) and `NextSequenceValue` (listed
  under Enterprise functions) on the
  [function reference](https://developers.arcgis.com/arcade/function-reference/) index rate
  ESRI ONLY as well. `ExecuteSQL` is in none of the rater's lists and rates PORTABLE, although it
  needs a database.
- The claim that ArcGIS rewrites a layer name into a GUID when an attribute rule is saved rests
  on one recorded geodatabase and no Esri page.
- The key locations the collectors read (`popupInfo.expressionInfos`,
  `labelExpressionInfo.expression`, `valueExpression`, `formInfo.expressionInfos`) come from
  earlier work and are not cited to the web map specification. The dashboard shape is the same.
- A tab or a carriage return cannot be in a Windows file name, and no Microsoft page is cited for
  that. The same is true of Excel opening accented text correctly after a byte order mark.
- The `--out` check and the write are two steps, so a file created between them is overwritten, and
  a dangling symlink at `--out` is followed. A write that fails halfway leaves a partial CSV that
  the next run refuses to overwrite. `--apply` with no `--out` writes nothing and says nothing.
- A JSON document nested deeper than the Python recursion limit ends in a traceback with exit
  code 1, which is also the code for "findings exist", not 2. A 5000 deep JSON list does it. The
  whole portal answer is read into memory with no size limit.
- A lone UTF-16 surrogate in a JSON string, such as a title of `\ud800`, makes the CSV write
  raise `UnicodeEncodeError`. The run ends in a traceback with exit code 1 and leaves a CSV that
  holds only the header row, which the next run refuses to overwrite.
- A JSON `name` or `layer` that is not a string, such as a number, a list or an object in a
  `gdbxray` report, is written as its Python text and without the guard, so the cell is not the
  original value. A negative number becomes the text `-5` and an infinite one `-inf`, and both
  start with a minus sign and are not guarded.
- A token that a portal echoes back in lowercase percent-escapes, or in any other re-encoding, is
  not masked in the error message. Only the raw token and the uppercase percent-encoded form are.
- If standard output is closed early, for example by `head`, the run ends in a traceback with exit
  code 1 or 120, so do not pipe the report into a reader that stops early when the exit code
  matters.
- A document that repeats a key inside one object is read with the last value only, so an earlier
  expression under that key is not collected.
- For a layer inside a group, the `layer` column names the group and not the sublayer, although the
  rating is read from the sublayer's own expression.
- When standard output is redirected, Windows gives Python the ANSI code page, so an accent is
  escaped rather than printed. No Microsoft page is cited for that.
- A flag typo that carries its value in one word, such as `--tokn=SECRET`, is refused with the
  value hidden. A glued typo, such as `--tokenSECRET` or `-tokenSECRET`, is refused with the whole
  argument hidden, so the message no longer names the flag you mistyped. `argparse` usage errors, such as an unknown flag, `--ap` or a flag with
  no value, exit 2 and not 64, although 2 also means that a read or write step failed.
- `urllib` follows any redirect the portal sends, including one to another host or from `https`
  to `http`, and with `--insecure` the redirected request is unverified too. The tool adds no
  token to a redirected request. The token reaches the new address only when the portal's
  `Location` header carries it, which was tested with two local stubs.
- Text from a document or a portal, such as a layer title or a portal error message, is printed
  to the terminal as it came. An escape sequence or a carriage return in it can redraw the lines
  around it.
- The CSV is created with the default permissions of the account, not owner only.
- `--out` without `--apply` prints `Would write` even when the file already exists. The run with
  `--apply` then refuses with exit 2.
- A JSON `expression` that is not a string, such as a number or a list, is skipped without a
  word, so a document can report zero expressions and exit 0.
- Any string under an `expression`, `valueExpression` or `scriptExpression` key that is not one
  bare identifier is rated as an expression. A reference name that holds a slash, such as
  `expr/0`, would count as one if a product stores it under such a key. That was not checked.
- The description of the ArcGIS Online Assistant and the Attribute Rules view in the opening rests
  on prior use of both tools, and no Esri page is cited for it.
- The byte order mark is this tool's choice. No Microsoft page is cited for how Excel reads a CSV
  that has none.
- The `PORTABLE WITH WORK` equivalents, shapely and GEOS for geometry and pyproj or geographiclib
  for geodesic measurement, are suggestions. The self-test does not exercise them and this README
  cites no page for them.
- The `gdbxray` fixture is recorded output from a synthetic File Geodatabase. The geodatabase is
  not shipped, so the fixture cannot be rebuilt from the files in this repository.
- The dashboard shape is matched by key name only: `expression` anywhere under a dashboard
  document is collected. It was not checked against Esri's dashboard specification, and the
  dashboard fixture in the self-test is a stand-in.
- A document with the right shape and a different rule layout, such as a newer `gdbxray`, exits
  2 when it carries a `counts.rules` that is not an integer or disagrees with the rules read, or
  when a rule has no text under `expression`. A report with no `counts.rules` at all is not
  recognised as a report, and is read as far as its generic shape allows.
- A portal that echoes the token back with HTML-escaped slashes, such as `&#x2F;`, is not masked
  in the "portal refused item" message, in the same way as the lowercase percent-escapes above.
- A token piped on standard input with a byte order mark in front, as a file saved by Notepad
  has, is used with the mark, so the portal refuses it and the tool exits 2. Nothing leaks.
- A portal url that already has a query or a fragment, such as `https://host/?a=b`, gets the item
  path added after it, so the request goes to the same host with a wrong path and exits 2.
- An uncaught exception anywhere ends in a traceback with exit code 1, the same code as "findings
  exist". The surrogate and deep nesting cases above are two ways to reach it.

## Verification

`--self-test` is 642 assertions with no portal and no geodatabase, and no network beyond a loopback `http.server` stub. It runs green with
the same count (642) on Windows under `python 3.9.25`, `3.10.20`, `3.11.8`, `3.12.10`, `3.13.2` and
`3.13.7`, of which `3.11.8` and `3.13.7` are cloned ArcGIS Pro conda environments and `3.13.2` is
a plain install, and on Ubuntu under `python 3.12.3`. The Windows 3.13 and Ubuntu 3.12 transcripts
are identical apart from line endings. Coverage was measured on Windows 3.13.2 only, because the coverage package is not installed on the Ubuntu host.
It also runs green with `HTTP_PROXY` set to a dead address, because the stub is read with the
proxy settings turned off.
Branch coverage is 100%, with no line excluded and no `pragma`: 1294 statements and 328
branches. The self-test loads the file once with `runpy` under another name, which is the only
way to take the false branch of the `__main__` guard, because a run as a script always takes the
true one. `pyflakes` is clean, and `python -W error` compiles the file with no warning. Of the
269 assertions added since the 373 before the CSV guard, 98 cover the guard, 72 cover the
false-clean, template literal and portal-failure fixes, 40 cover the unclosed-literal,
geometry-function, token and proxy fixes, 20 cover the featureset-function rating, the `$view`
rating and the glued-flag redaction, 5 cover the rule count and renamed key checks, 4 cover the stray-word file name redaction, and 30 cover the Track, `QueryGraph`, `NextSequenceValue` and `$graph` ratings and the redaction of a stray word that follows a file name. They are the same on every host. A tab or
a carriage return cannot be in a Windows file name, so those two are exercised through the
document text and through `csv_row`, and the file name case uses `=evil.json`.

Each claim and where it was exercised:

| Claim | Exercised by |
|---|---|
| Each of the six starting characters is guarded | `guard_cell`, six assertions, and a mutation run that removed the tab and then the carriage return from the list, which turned the self-test red both times |
| Every text column is guarded | `csv_row`, ten columns by six characters |
| Numbers stay numbers, even negative | `csv_row` with `chars` of -4, and the rows of a real file |
| A written file has no cell that starts with a formula character | a real `--apply` run of a hostile document, read back with `csv.reader` |
| A hostile source file name is guarded | the same run, with a file named `=evil.json` |
| A hostile JSON key in `path` is guarded | a real run over a document with the key `=evil` |
| Portal text is guarded | a real `--apply` run against a local `http.server` stub |
| Nothing is written without `--apply`, and `--ap` is refused | unchanged, from the earlier assertions |
| A file of no known shape, a saved portal error, a rule count that does not match or is not an integer, or a rule with no text under `expression` exits 2, never 0 | `main` over seven real files, plus the pure `unreadable` and `count_rules` |
| A document that was read and held no Arcade is counted | a web map with an empty layer list, `1 document(s)` |
| A non-UTF-8 file names itself | a real Latin-1 file |
| A space, control character or trailing newline in the portal or the id never reaches `urllib`, so no token is printed | `item_url` and `main`, with the token as a search string in the output |
| A user name or password in the portal url is refused with exit 64 and is never printed | `item_url`, three urls, and the same refusal through `main` |
| An apostrophe in a template literal hides no call after it, and a name in its text is no call | `rate` over a popup line `${$feature.OWNER}'s parcel` followed by a `FeatureSetByName` call, and over a name written as plain text |
| A truncated answer, a read timeout, a non-HTTP reply and a numeric error message exit 2 with no token | a local `http.server` stub, one item for each |
| A token echoed back percent encoded is masked | the same stub, with a token that needs encoding |
| `FeatureSetByRelationshipClass` and `KnowledgeGraphByPortalItem` are ESRI ONLY | `rate`, beside the other esri only calls |
| The summary names what failed | `_verdict`, which a green run cannot otherwise reach |
| An expression with an unclosed string, comment or template is never rated PORTABLE, and a call a stray quote would hide still counts | `rate` over five unclosed shapes, and `main` over two real files, which exit 1 |
| All 15 geometry functions that were rated PORTABLE are PORTABLE WITH WORK | `rate`, one assertion per function |
| A token can be given on standard input, and `--token` warns | `read_token`, and `main` against the loopback stub, which accepts only the right token |
| A mistyped token flag is not echoed back | `_parse` with `--tokn=SECRET123`, `--tokenSECRET456` and `-tokenSECRET456`, checked on the captured stderr |
| `GetFeatureSet`, `GetFeatureSetInfo` and `GdbVersion` are ESRI ONLY, and `FilterBySubtypeCode` is PORTABLE WITH WORK | `rate`, beside the other calls of each rating, and their commented and quoted twins |
| A redirect carries the token only when the portal's `Location` does | two redirect items on the loopback stub |
| A proxy in the environment does not break the self-test | `fetch_item` against the stub with `HTTP_PROXY` set to a dead address, and the same call with the default proxy handling, which fails as expected |

Sources, all re-fetched on 2026-11-11:

- [OWASP, CSV Injection](https://community.owasp.org/attacks/CSV_Injection): the characters that
  start a formula, which include the six guarded here plus a line feed and the full-width
  forms; the single quote prefix as one mitigation and a tab for Excel as the preferred one; and
  the warning that none is reliable after Excel saves and reopens the file.
- [Python `csv` module](https://docs.python.org/3/library/csv.html): "If csvfile is a file object,
  it should be opened with newline=''". The page does not mention `utf-8-sig`. The byte order
  mark is this tool's choice for Excel, and the self-test pins it.
- [Arcade function reference](https://developers.arcgis.com/arcade/function-reference/): the
  [Geometry functions](https://developers.arcgis.com/arcade/function-reference/geometry_functions/)
  page, re-fetched on 2026-11-11, lists `Angle`, `Bearing`, `ConvertDirection`,
  `DistanceToCoordinate`, `EnvelopeIntersects`, `Equals`, `IsSelfIntersecting`, `IsSimple`,
  `MeasureToCoordinate`, `NearestCoordinate`, `NearestVertex`, `PointToCoordinate`,
  `RingIsClockwise`, `SetGeometry` and `Simplify`, which this tool rates PORTABLE WITH WORK.
  `FeatureSetByName`, `FeatureSetByRelationshipName` and `FeatureSetByRelationshipClass` are
  FeatureSet functions, and `KnowledgeGraphByPortalItem` is a knowledge graph function. The
  [FeatureSet functions](https://developers.arcgis.com/arcade/function-reference/featureset_functions/)
  page, fetched on 2026-11-11, describes `GetFeatureSet` as getting "the FeatureSet in which the
  input feature belongs", `GetFeatureSetInfo` as metadata "for the original source database and
  service", `GdbVersion` as the name of the "current geodatabase version" and
  `FilterBySubtypeCode` as a feature set of the features "matching the given subtype code".
  This tool rates the first three ESRI ONLY and the last PORTABLE WITH WORK.
- [Arcade template literals](https://developers.arcgis.com/arcade/guide/template-literals/):
  introduced at version 1.11, enclosed by backticks, and "placeholders ... indicated by a dollar
  sign and curly braces". The page does not say how a backtick is escaped.
- Arcade profile pages, each with a "Profile variables" table. `$map` and `$view` are named on the
  [popup](https://developers.arcgis.com/arcade/profiles/popup/) and
  [form calculation](https://developers.arcgis.com/arcade/profiles/form-calculation/) pages.
  `$datastore` is named on the popup, form calculation,
  [attribute rule calculation](https://developers.arcgis.com/arcade/profiles/attribute-rule-calculation/)
  and [field calculation](https://developers.arcgis.com/arcade/profiles/field-calculation/)
  pages. `$layer` is named on the popup and form calculation pages. `$editcontext` is named on
  the attribute rule calculation and form calculation pages. `$originalFeature` and
  `$featureSet` are named on the attribute rule calculation and
  [form constraint](https://developers.arcgis.com/arcade/profiles/form-constraint/) pages.
  `$targetDatastore` is named on the
  [field mapping](https://developers.arcgis.com/arcade/profiles/field-mapping/) page. A search of
  every profile page listed in the [profiles index](https://developers.arcgis.com/arcade/profiles/)
  found no `$aggregatedFeatures`, `$camera` or `$sensor`.

The attribute rule fixtures are recorded output. A synthetic File Geodatabase was built with
`arcpy`, six attribute rules were added to two feature classes, `gdbxray --json` read it back, and
that output is what the self-test asserts against.

## Contributing

Open an issue or pull request on GitHub.

## Author

Built by [Asir Khan](https://www.linkedin.com/in/asir-khan-310317264/).

## License

MIT.

## Related

Other single-file tools in this portfolio that pair with this one:

- [gdbxray](https://github.com/uhsear/gdbxray) - reads the attribute rules this consumes, with --json
- [arcade-rule-deploy](https://github.com/uhsear/arcade-rule-deploy) - deploy the rules this rates
- [sharewatch](https://github.com/uhsear/sharewatch) - the same org, audited for sharing instead of portability
