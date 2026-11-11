# arcadecheck

Inventory every Arcade expression you own and say which ones can leave with you.

A county decides to move a layer off ArcGIS Online. The first question is how much Arcade is
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

```
$ python arcadecheck.py --self-test
arcadecheck self-test: no portal, no geodatabase, no credentials
--------------------------------------------------------------------
PASS  a line comment is blanked out
PASS  a division is not the start of a comment  <-- pinned defect
PASS  a member called after a dot is not a call to a function of that name  <-- pinned defect
PASS  the if of an if statement is not a call  <-- pinned defect
...
PASS  FeatureSetByName makes an expression esri only
PASS  and FeatureSetByName is named as the reason
PASS  FeatureSetByName inside a comment does not count  <-- pinned defect
PASS  FeatureSetByName inside a string literal does not count  <-- pinned defect
PASS  $datastore inside a comment does not count  <-- pinned defect
PASS  $datastore inside a string literal does not count  <-- pinned defect
...
PASS  a ONE LINE expression calling FeatureSetByName is esri only  <-- pinned defect
PASS  a THIRTY LINE arithmetic expression is portable  <-- pinned defect
PASS  and the portable one is more than twenty times longer than the unportable one  <-- pinned defect
PASS  an empty expression rates portable with nothing in it
PASS  a null expression rates portable instead of raising
PASS  non-ascii text inside an expression is portable
...
PASS  a document carrying both shapes is read as the gdbxray report, because its rules are not a web map's layers  <-- pinned defect
PASS  a bare identifier is the NAME of an expression, not one  <-- pinned defect
PASS  a label is a label, although its path runs through drawingInfo  <-- pinned defect
PASS  the legacy [FIELD] label syntax is NOT collected as arcade  <-- pinned defect
PASS  a form element that names an expression is not collected as one  <-- pinned defect
PASS  a form expression has no layer, rather than repeating its own title  <-- pinned defect
...
PASS  all six attribute rules in the geodatabase are collected
PASS  which is the rule count gdbxray reported for that geodatabase
PASS  a rule calling FeatureSetByName over $datastore is esri only
PASS  a rule naming two esri functions in a comment and a string is portable  <-- pinned defect
PASS  a rule that is ONE line is still esri only  <-- pinned defect
PASS  even though the portable rule beside it is three lines long  <-- pinned defect
PASS  and shorter than it in characters too  <-- pinned defect
PASS  the layer name inside the rule is the guid the geodatabase stored, not the name it was written with
...
PASS  --apply is off by default, so nothing is written  <-- pinned defect
PASS  --insecure is off by default, so tls is verified  <-- pinned defect
...
PASS  text starting with an equals sign gets one apostrophe in front  <-- pinned defect
PASS  text starting with a carriage return gets one apostrophe in front  <-- pinned defect
PASS  the expression column is guarded when it starts with a tab  <-- pinned defect
PASS  a number column stays a number even when it is negative  <-- pinned defect
...
PASS  a unique prefix of --apply, --ap, is refused rather than read as --apply, so a typo cannot write a file  <-- pinned defect
...
PASS  there is no --password at all, because argv is readable by every process on the box  <-- pinned defect
PASS  a portal url with no scheme is refused before any request is made, because urllib would quote the token back  <-- pinned defect
PASS  and that mark is read as a mark rather than as the first character of broken json  <-- pinned defect
PASS  and writes nothing  <-- pinned defect
PASS  a source file named with an equals sign is guarded in the source column  <-- pinned defect
PASS  no cell in the whole file starts with a character a spreadsheet runs as a formula  <-- pinned defect
PASS  and has no blank line between rows, which is what newline='' prevents on windows  <-- pinned defect
PASS  the header names every column in the order a reader expects, written out here rather than read back from COLUMNS  <-- pinned defect
PASS  a second run refuses to overwrite the csv  <-- pinned defect
...
PASS  a read with no token puts no token on the wire at all, not an empty one  <-- pinned defect
PASS  and a token is percent encoded whole, so a slash or an ampersand inside one cannot split the query  <-- pinned defect
PASS  an item read from the portal comes back as its document
PASS  and a token the portal echoed back is not in the message  <-- pinned defect
PASS  the token never reaches stdout  <-- pinned defect
PASS  a --token on the command line beats a stale one left in the environment  <-- pinned defect
PASS  and no credential reaches the csv on disk  <-- pinned defect
PASS  and its layer, name and expression are guarded on the way to disk  <-- pinned defect
PASS  a console that cannot carry an accent gets it escaped rather than a traceback  <-- pinned defect
PASS  the harness records a false check, a missing exception and a wrong exception as three failures, so a broken tool turns this self-test red  <-- pinned defect
--------------------------------------------------------------------
471 assertions, 0 failed
```

## Requirements

Python 3.9 or newer. Standard library only: `argparse`, `csv`, `io`, `json`, `os`, `re`, `ssl`,
`sys` and `urllib`, with `http.server`, `threading`, `tempfile` and `shutil` used inside the
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

That run is a real one, against a File Geodatabase built for the self-test. `OwnerName` is one
line long and it is still ESRI ONLY.

## Usage

Documents on disk, a portal, or both in one run. Nothing is written without `--apply`.

```
python arcadecheck.py webmap.json dashboard.json form.json rules.json
python arcadecheck.py webmap.json --out arcade.csv --apply
python arcadecheck.py --portal https://county.maps.arcgis.com --item 1a2b3c --item 4d5e6f
```

| Flag | Default | What it does |
|---|---|---|
| `FILE` | none | Web map, dashboard, form or `gdbxray --json` documents. Any number of them. |
| `--portal` | none | Portal to read items from. Must start with `https://` or `http://`. |
| `--item` | none | Item id to read from `--portal`. Repeatable. |
| `--token` | none | Portal token. Env: `ARCADECHECK_TOKEN`, which keeps it out of the process list. |
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
- **Dashboards.** The Arcade data expressions under a widget's data source.
- **Attribute rules.** From `gdbxray --json` output, not from the geodatabase. `gdbxray` already
  reads `GDB_Items`, and there is no reason for a second tool to open a `.gdb`.

Each expression is rated on **what it calls**:

| Rating | Means | Examples of what earns it |
|---|---|---|
| `ESRI ONLY` | There is no equivalent outside ArcGIS. | `FeatureSetByName`, `FeatureSetByPortalItem`, `FeatureSetByRelationshipName`, `Portal()`, `GetUser`, `$map`, `$datastore`, `$layer`, `$editcontext`, `$originalfeature`, `$aggregatedfeatures` |
| `PORTABLE WITH WORK` | Something else does this, under another name. | geometry predicates and operations (shapely and GEOS), geodesic measurement (pyproj or geographiclib), domain and subtype lookups (rebuild as a table), attachments |
| `PORTABLE` | It moves as it is. | arithmetic and text over the row's own fields, `IIf`, `When`, `Count`, `Filter`, date formatting |

The full list of names in each rating is at the top of the file, next to the reason printed for
each one. Adding to it is the intended way to extend the tool.

One guard is on the output side. The CSV is the only thing this tool writes, and it is written
only with `--apply`. Every text column goes through `guard_cell` first, whatever put the text
there: the document, the portal, the file name you passed, or a key in the JSON path. The
character list is OWASP's. Its page recommends the single quote prefix used here, and it also
warns that the prefix is not reliable in Excel after the file is saved and reopened, so treat the
guard as a first line and not as a guarantee. A cell that starts with a space is not guarded,
because a space is not on that list.

Three things it refuses to be fooled by:

- **Length is not portability.** Nothing in the rater can see how long an expression is. A one
  line `FeatureSetByName($datastore, "Hydrants")` is ESRI ONLY. A thirty line block of arithmetic
  is PORTABLE. The self-test pins both, on a real geodatabase's rules as well as on made up ones.
- **A name inside a comment or a string is not a call.** Comments and string literals are blanked
  out before anything is searched for, so `// FeatureSetByName was removed here` rates PORTABLE.
  Without that, a migration note in a comment costs somebody a week.
- **A name after a dot is not a call.** `$feature.Buffer(3)` reads a field called `Buffer`. The
  geometry function of that name is `Buffer($feature, ...)`, at the start of the expression.

The credential rules are the same ones the rest of this portfolio uses. There is no `--password`
flag, because `argv` is readable by every process on the machine. The token can come from
`ARCADECHECK_TOKEN` instead of the command line. A portal url with no scheme is refused before a
request is built, because `urllib` quotes the whole url back into its own exception and that url
carries the token. A token that a portal echoes back inside an error message is masked before the
message is printed.

## Exit codes

| Code | Means |
|---|---|
| 0 | Every expression found is PORTABLE, or nothing was found. |
| 1 | At least one expression is PORTABLE WITH WORK or ESRI ONLY. |
| 2 | A document could not be read, a portal call failed, or the CSV could not be written. |
| 64 | Usage error: no input, an `--item` with no `--portal`, or a portal url with no scheme. |

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
- Only the Arcade facts that the official documentation confirmed are cited under Verification.
  The function reference lists `FeatureSetByName` under its FeatureSet functions. The profile
  guide named `$feature` and `$map` and did not name `$datastore`, `$layer`, `$editcontext`,
  `$originalfeature` or `$aggregatedfeatures`, so those five ratings rest on earlier work and
  were not re-verified in this release.

## Verification

`--self-test` is 471 assertions with no network, no portal and no geodatabase. It runs green on
Windows under `python 3.13`, on ArcGIS Pro's `python 3.13.7`, on Windows under `python 3.9.25` and on Ubuntu under `python 3.12`,
with the same count. The Windows 3.13 and Ubuntu 3.12 transcripts are identical line for line.
Branch coverage is 100%. The one line outside it is the `__main__` guard, which carries
`# pragma: no cover`, because the coverage runner starts the file and the guard never fires
when it does. The 98 assertions added for the CSV guard and the summary are the same on every
host. A tab or a carriage return cannot be in a Windows file name, so those two are exercised
through the document text and through `csv_row`, and the file name case uses `=evil.json`.

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
| The summary names what failed | `_verdict`, which a green run cannot otherwise reach |

Sources checked on 2026-11-11:

- [OWASP, CSV Injection](https://owasp.org/www-community/attacks/CSV_Injection): the six
  characters, the single quote prefix, and the warning that it is unreliable after Excel saves
  and reopens the file.
- [Python `csv` module](https://docs.python.org/3/library/csv.html): open the file with
  `newline=''`. The page does not mention `utf-8-sig`. The byte order mark is this tool's choice
  for Excel, and the self-test pins it.
- [Arcade function reference](https://developers.arcgis.com/arcade/function-reference/):
  `FeatureSetByName` is a FeatureSet function.
- [Arcade profiles guide](https://developers.arcgis.com/arcade/guide/profiles/): `$feature` and
  `$map` are profile variables, and which ones exist depends on the context.

The attribute rule fixtures are real. A File Geodatabase was built with `arcpy`, six attribute
rules were added to two feature classes, `gdbxray --json` read it back, and that output is what
the self-test asserts against.

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
