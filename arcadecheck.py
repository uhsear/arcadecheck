#!/usr/bin/env python
"""Inventory every Arcade expression you own and say which ones can leave with you.

Reads web maps, dashboards, forms and attribute rules, finds the Arcade in them,
and rates each expression PORTABLE, PORTABLE WITH WORK or ESRI ONLY with the
reason. The deliverable is a CSV and a summary. It sizes the migration; it does
not perform it.

The tool people reach for first is the ArcGIS Online Assistant, and for one item
it is the right tool. It shows an item's raw JSON, it lets you edit it in place,
and it needs nothing installed. ArcGIS Pro does the same for a geodatabase: the
Attribute Rules view lists every rule on a feature class with its expression.
Both answer "what is in this thing". Neither answers the question a migration
asks, which is "how much Arcade is there across all of these, and how much of it
dies outside Esri". There is no view that spans popups, labels, renderers,
dashboards, forms and attribute rules at once, and nothing in the box reads an
expression and tells you it calls FeatureSetByName.

Rating is done on what an expression CALLS, never on how long it is. A one line
expression that calls FeatureSetByName is ESRI ONLY. A forty line expression
that does arithmetic on its own row is PORTABLE. A name that appears only inside
a comment or a string literal is not a call and does not count.

Attribute rules are not read from the geodatabase here. gdbxray already does
that, so this tool consumes its --json output instead:

    python gdbxray.py water.gdb --json --out rules.json --apply
    python arcadecheck.py rules.json

    python arcadecheck.py --self-test
    python arcadecheck.py webmap.json dashboard.json rules.json
    python arcadecheck.py webmap.json --out arcade.csv --apply
    python arcadecheck.py --portal https://county.maps.arcgis.com --item 1a2b3c

Exit codes: 0 every expression is portable, 1 at least one is not, 2 a read or
write step failed, 64 usage error.
"""

from __future__ import print_function

import argparse
import csv
import io
import json
import os
import re
import ssl
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import urlopen

# =============================================================================
# CONFIGURATION. Deliberately not flags. Change here, not at the call site.
# =============================================================================

# The three ratings, worst last. Order is meaning here: rate() keeps the worst
# rating any one call earns, so RANK has to stay in this order.
PORTABLE = "PORTABLE"
WITH_WORK = "PORTABLE WITH WORK"
ESRI_ONLY = "ESRI ONLY"
RANK = {PORTABLE: 0, WITH_WORK: 1, ESRI_ONLY: 2}

# JSON keys that hold Arcade source in a web map, a dashboard, a form or a
# gdbxray report. Lowercased, because the specification is not consistent about
# case between products. Add to this list rather than loosening the match: every
# key here is one whose value is known to be code.
#
#   expression        popupInfo.expressionInfos[], labelExpressionInfo,
#                     formInfo.expressionInfos[], a dashboard data expression
#   valueExpression   a renderer or a visual variable
#   scriptExpression  an attribute rule, the name ArcGIS writes in the XML
#
# labelExpression is deliberately NOT here. That is the legacy [FIELD] label
# syntax, not Arcade, and collecting it would inflate the count with expressions
# that were never Arcade in the first place.
ARCADE_KEYS = frozenset(("expression", "valueexpression", "scriptexpression"))

# Where an expression lives, worked out from the JSON path it was found at. The
# first marker that matches the path wins, so this list is in order of
# specificity, not alphabetical.
#
# The label entries have to come first. Labels live UNDER drawingInfo in a web
# map, at layerDefinition.drawingInfo.labelingInfo[0].labelExpressionInfo, so a
# list that tests drawingInfo first reports every label as a renderer and the
# migration plan sends the wrong person to fix them.
PATH_KINDS = (
    ("labelexpressioninfo", "label"),
    ("labelinginfo", "label"),
    ("visualvariable", "renderer"),
    ("renderer", "renderer"),
    ("drawinginfo", "renderer"),
    ("popup", "popup"),
    ("forminfo", "form"),
    ("formelement", "form"),
    ("widget", "dashboard"),
)

# The default location when no marker in PATH_KINDS matches, by document type.
DOC_KINDS = {"dashboard": "dashboard", "form": "form"}

# Columns of the CSV, in order.
COLUMNS = ("source", "document", "where", "layer", "name", "rating",
           "reasons", "calls", "chars", "lines", "path", "expression")

# The REST path that returns an item's data document. Read only.
ITEM_DATA_PATH = "/sharing/rest/content/items/%s/data"

# How long to wait on the portal, in seconds.
TIMEOUT = 30

# Excel opens a CSV in the ANSI codepage unless the file starts with a byte
# order mark, and an Arcade expression carrying an accent or a degree sign is
# common enough that mojibake would be the normal case. utf-8-sig writes the
# mark; every CSV reader that matters strips it again.
CSV_ENCODING = "utf-8-sig"

# =============================================================================
# End of CONFIGURATION.
# =============================================================================

# Words that are followed by "(" in ordinary Arcade and are not function calls.
# Without this, every if statement in the org reports a call to a function named
# "if", which buries the calls that matter in the CSV.
KEYWORDS = frozenset(("if", "for", "while", "return", "var", "else", "function",
                      "break", "continue", "in", "new", "typeof"))

# A function call: a name followed by an open bracket. The lookbehind drops
# member access, so $feature.Buffer(x) is a field called Buffer and not a call
# to the geometry function of that name.
CALL_RE = re.compile(r"(?<![A-Za-z0-9_$.])([A-Za-z_][A-Za-z0-9_]*)\s*\(")

# A profile variable: $feature, $map, $datastore and the rest.
GLOBAL_RE = re.compile(r"(?<![A-Za-z0-9_])(\$[A-Za-z_][A-Za-z0-9_]*)")

# One dotted path element, or one [index].
TOKEN_RE = re.compile(r"([^.\[\]]+)|\[(\d+)\]")

# A single identifier and nothing else. A key that holds the NAME of an
# expression rather than its text looks like this, and a string that is one bare
# word is not Arcade worth rating.
BARE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# What an expression calls that cannot leave ArcGIS at all. The reason is
# printed and written to the CSV, so it names the thing that has to be replaced.
ESRI_ONLY_CALLS = {
    "featuresetbyname":
        "reads another layer by name out of the map or the geodatabase",
    "featuresetbyportalitem":
        "fetches a layer from a portal item id",
    "featuresetbyrelationshipname":
        "walks a geodatabase relationship class",
    "featuresetbyassociation":
        "walks a utility network association",
    "featuresetbyid":
        "reads a layer out of the map by layer id",
    "portal":
        "names an ArcGIS portal to read from",
    "getuser":
        "reads a portal user, which exists only in a portal",
    "$map":
        "the map the expression is running in",
    "$datastore":
        "the workspace the edited feature came from",
    "$layer":
        "the layer the feature is drawn from",
    "$editcontext":
        "the attribute rule edit context",
    "$originalfeature":
        "the pre-edit row, which only an attribute rule is given",
    "$featureset":
        "the whole layer behind the feature",
    "$aggregatedfeatures":
        "the cluster a drawn feature was aggregated into",
    "$targetdatastore":
        "the target workspace of an attribute rule edit",
}

# What an expression calls that has an open-source equivalent, but not the same
# call. These are the ones that turn a migration estimate into a number rather
# than a guess: they are work, and they are not a rewrite from nothing.
WITH_WORK_CALLS = {}

# Geometry predicates and operations. GEOS, through shapely, does all of these.
for _name in ("intersects", "contains", "within", "touches", "crosses",
              "overlaps", "disjoint", "relate", "buffer", "clip", "cut",
              "convexhull", "difference", "densify", "extent", "generalize",
              "geometry", "intersection", "multiparttosinglepart", "offset",
              "rotate", "symmetricdifference", "union", "area", "length",
              "length3d", "centroid", "point", "polygon", "polyline",
              "multipoint", "distance"):
    WITH_WORK_CALLS[_name] = ("a geometry operation; shapely and GEOS do the "
                              "same work under another name")

# The geodetic half is a separate reason because shapely does NOT do it. These
# need pyproj or geographiclib, and getting them wrong is a wrong number rather
# than a missing function.
for _name in ("areageodetic", "lengthgeodetic", "distancegeodetic",
              "buffergeodetic", "densifygeodetic"):
    WITH_WORK_CALLS[_name] = ("a geodesic measurement; needs pyproj or "
                              "geographiclib, not shapely")

# Domains and subtypes are geodatabase furniture. They can be rebuilt as lookup
# tables, and somebody has to do it.
for _name in ("domain", "domainname", "domaincode", "subtypes", "subtypecode",
              "subtypename"):
    WITH_WORK_CALLS[_name] = ("a geodatabase domain or subtype lookup; rebuild "
                              "it as a table")

WITH_WORK_CALLS["featureset"] = (
    "builds a feature set from Esri feature JSON; the JSON travels, the "
    "constructor does not")
WITH_WORK_CALLS["schema"] = "returns an Esri field schema object"
WITH_WORK_CALLS["attachments"] = (
    "reads attachments, which are a geodatabase table plus a blob store")

del _name


# ----------------------------------------------------------------- pure core

def strip_noise(text):
    """Blank out comments and string literals so a name inside one does not count.

    This is the whole defence against a false ESRI ONLY. An expression that
    mentions FeatureSetByName in a comment, or writes the word into an error
    message, calls nothing. Rating it Esri only sends somebody to rewrite an
    expression that was already portable, and inflates the migration estimate
    this tool exists to produce.

    Removed characters are replaced by spaces rather than deleted, and newlines
    inside a comment or a literal are kept, so the blanked text has the same
    length and the same line numbering as what came in.

    An unterminated literal or block comment swallows the rest of the text. That
    is what the Arcade parser does with it too, and an expression that does not
    parse is not something this tool should paper over by guessing.
    """
    if not text:
        return ""
    out = []
    index = 0
    size = len(text)
    while index < size:
        char = text[index]
        after = text[index + 1] if index + 1 < size else ""
        if char == "/" and after == "/":
            while index < size and text[index] != "\n":
                out.append(" ")
                index += 1
        elif char == "/" and after == "*":
            out.append("  ")
            index += 2
            while index < size:
                if text[index] == "*" and index + 1 < size and text[index + 1] == "/":
                    out.append("  ")
                    index += 2
                    break
                out.append("\n" if text[index] == "\n" else " ")
                index += 1
        elif char == '"' or char == "'":
            quote = char
            out.append(" ")
            index += 1
            while index < size:
                if text[index] == "\\" and index + 1 < size:
                    out.append(" ")
                    out.append("\n" if text[index + 1] == "\n" else " ")
                    index += 2
                    continue
                if text[index] == quote:
                    out.append(" ")
                    index += 1
                    break
                out.append("\n" if text[index] == "\n" else " ")
                index += 1
        else:
            out.append(char)
            index += 1
    return "".join(out)


def find_calls(text):
    """Every function called and every profile variable read, in the order written.

    Case is kept as it was written, because that is what somebody has to search
    for, but duplicates are matched case insensitively. Arcade does not care
    whether you wrote FeatureSetByName or featuresetbyname, and neither does the
    thing that breaks when the expression moves.
    """
    found = []
    for match in CALL_RE.finditer(text):
        name = match.group(1)
        if name.lower() in KEYWORDS:
            continue
        found.append((match.start(1), name))
    for match in GLOBAL_RE.finditer(text):
        found.append((match.start(1), match.group(1)))
    found.sort()
    names = []
    seen = set()
    for _position, name in found:
        if name.lower() in seen:
            continue
        seen.add(name.lower())
        names.append(name)
    return names


def rate(expression):
    """Rate one expression on what it calls. Length is never an input.

    The pinned defect is the temptation to treat a short expression as an easy
    one. FeatureSetByName($datastore, "Hydrants") is forty characters and cannot
    leave ArcGIS. A forty line block of arithmetic over $feature moves as it is.
    Nothing in this function can see how long the text is.
    """
    text = expression if isinstance(expression, str) else ""
    calls = find_calls(strip_noise(text))
    reasons = []
    rating = PORTABLE
    for name in calls:
        key = name.lower()
        if key in ESRI_ONLY_CALLS:
            reasons.append({"name": name, "rating": ESRI_ONLY,
                            "why": ESRI_ONLY_CALLS[key]})
        elif key in WITH_WORK_CALLS:
            reasons.append({"name": name, "rating": WITH_WORK,
                            "why": WITH_WORK_CALLS[key]})
        else:
            continue
        if RANK[reasons[-1]["rating"]] > RANK[rating]:
            rating = reasons[-1]["rating"]
    reasons.sort(key=lambda reason: (-RANK[reason["rating"]],
                                     reason["name"].lower()))
    return {
        "rating": rating,
        "reasons": reasons,
        "calls": calls,
        "chars": len(text),
        "lines": text.count("\n") + 1 if text else 0,
    }


def walk(doc, path=""):
    """Every scalar in a nested document as (path, key, value, parent)."""
    if isinstance(doc, dict):
        for key in doc:
            value = doc[key]
            child = "%s.%s" % (path, key) if path else key
            if isinstance(value, (dict, list)):
                for item in walk(value, child):
                    yield item
            else:
                yield (child, key, value, doc)
    elif isinstance(doc, list):
        for index, value in enumerate(doc):
            child = "%s[%d]" % (path, index)
            if isinstance(value, (dict, list)):
                for item in walk(value, child):
                    yield item
            else:
                yield (child, "", value, doc)


def lookup(doc, path):
    """The node at a dotted path, or None. The inverse of what walk() prints."""
    node = doc
    for name, index in TOKEN_RE.findall(path):
        if name:
            if not isinstance(node, dict) or name not in node:
                return None
            node = node[name]
        else:
            position = int(index)
            if not isinstance(node, list) or position >= len(node):
                return None
            node = node[position]
    return node


def container_label(doc, path, parent=None):
    """The title of the first indexed thing on the path: the layer or the widget.

    An expression on its own is not actionable. The person doing the migration
    needs to know which layer carries it, and in a web map that is the title of
    the operational layer the expression is buried inside.

    The object that holds the expression is not its own container. In a form
    document the first indexed thing on the path IS that object, and returning
    its title would print the expression's own name in the layer column as
    though a layer of that name existed.
    """
    cut = path.find("]")
    if cut < 0:
        return ""
    node = lookup(doc, path[:cut + 1])
    if not isinstance(node, dict) or node is parent:
        return ""
    for key in ("title", "name", "id", "label"):
        value = node.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def detect(doc):
    """What kind of document this is, by the keys only it has.

    The tests run from most specific to least, and gdbxray's report is the only
    shape recognised by two keys at once, so it goes first. A document that
    carries both shapes is read as the report: its rules hold the expressions,
    and walking it as a web map would name every one of them after a layer that
    is not there.
    """
    if not isinstance(doc, dict):
        return "json"
    counts = doc.get("counts")
    if isinstance(counts, dict) and "rules" in counts and isinstance(
            doc.get("datasets"), list):
        return "rules"
    if isinstance(doc.get("operationalLayers"), list):
        return "webmap"
    if isinstance(doc.get("widgets"), list):
        return "dashboard"
    if isinstance(doc.get("formInfo"), dict) or isinstance(
            doc.get("formElements"), list):
        return "form"
    return "json"


def is_expression(value):
    """True when this string is Arcade source rather than a reference to some.

    Several keys in the web map specification hold the NAME of an expression
    defined elsewhere, and the form elements are the ones that matter here. A
    bare identifier is that; it is never Arcade worth rating, because an
    expression made of one word returns nothing. Whitespace only is skipped for
    the same reason: there is nothing in it to port.
    """
    if not isinstance(value, str):
        return False
    text = value.strip()
    if not text:
        return False
    return BARE_NAME_RE.match(text) is None


def where_from(path, document):
    """Which part of the item an expression was found in."""
    lowered = path.lower()
    for marker, kind in PATH_KINDS:
        if marker in lowered:
            return kind
    return DOC_KINDS.get(document, "expression")


def name_from(parent, key):
    """A readable name for one expression, from the object that holds it."""
    if isinstance(parent, dict):
        for field in ("title", "name", "label", "valueExpressionTitle",
                      "fieldName"):
            value = parent.get(field)
            if isinstance(value, str) and value.strip():
                return value
    return key


def record(source, document, where, layer, name, path, expression):
    """One CSV row: where the expression lives, and what it is rated."""
    row = {"source": source, "document": document, "where": where,
           "layer": layer, "name": name, "path": path,
           "expression": expression}
    row.update(rate(expression))
    return row


def collect(doc, source=""):
    """Every rated Arcade expression in one document.

    A gdbxray report is read through its own shape rather than by walking it,
    because the rule name, the feature class and the rule type are all worth
    carrying into the CSV, and none of them is reachable from the expression's
    path alone.
    """
    document = detect(doc)
    if document == "rules":
        return collect_rules(doc, source)
    records = []
    for path, key, value, parent in walk(doc):
        if key.lower() not in ARCADE_KEYS:
            continue
        if not is_expression(value):
            continue
        records.append(record(source, document, where_from(path, document),
                              container_label(doc, path, parent),
                              name_from(parent, key), path, value))
    return records


def collect_rules(doc, source=""):
    """Every attribute rule in a gdbxray --json report, rated.

    The path written out is the path into that report, not into the geodatabase,
    so a row in the CSV can be found again in the file it came from.
    """
    records = []
    datasets = doc.get("datasets")
    if not isinstance(datasets, list):
        return records
    for position, dataset in enumerate(datasets):
        if not isinstance(dataset, dict):
            continue
        rules = dataset.get("rules")
        if not isinstance(rules, list):
            continue
        for index, rule in enumerate(rules):
            if not isinstance(rule, dict):
                continue
            expression = rule.get("expression")
            if not is_expression(expression):
                continue
            records.append(record(
                source, "rules", "rule",
                dataset.get("name") or "",
                rule.get("name") or "",
                "datasets[%d].rules[%d].expression" % (position, index),
                expression))
    return records


def summarise(records):
    """Counts by rating, by location and by blocking call."""
    summary = {
        "total": len(records),
        "by_rating": dict((name, 0) for name in RANK),
        "by_where": {},
        "by_reason": {},
        "chars": 0,
        "sources": [],
        "worst": PORTABLE,
    }
    for row in records:
        summary["by_rating"][row["rating"]] += 1
        summary["by_where"][row["where"]] = summary["by_where"].get(
            row["where"], 0) + 1
        summary["chars"] += row["chars"]
        if row["source"] not in summary["sources"]:
            summary["sources"].append(row["source"])
        if RANK[row["rating"]] > RANK[summary["worst"]]:
            summary["worst"] = row["rating"]
        # One expression that calls FeatureSetByName three times is still one
        # expression to rewrite. Nothing is deduplicated here because nothing
        # has to be: find_calls already reports a name once however often it
        # was written, so a row cannot arrive carrying the same reason twice.
        for reason in row["reasons"]:
            key = reason["name"].lower()
            entry = summary["by_reason"].setdefault(
                key, {"name": reason["name"], "rating": reason["rating"],
                      "why": reason["why"], "count": 0})
            entry["count"] += 1
    return summary


def reason_text(row):
    """The reasons column of the CSV: one sentence per blocking call."""
    return " | ".join("%s %s" % (reason["name"], reason["why"])
                      for reason in row["reasons"])


def csv_row(row):
    """One record flattened to strings, in COLUMNS order."""
    flat = dict(row)
    flat["reasons"] = reason_text(row)
    flat["calls"] = ", ".join(row["calls"])
    return [flat.get(name, "") for name in COLUMNS]


def render(records, summary, show_all=False):
    """The report as lines of text. What cannot move first, then the counts."""
    lines = ["arcadecheck: %d expression(s) in %d document(s)"
             % (summary["total"], len(summary["sources"])),
             "-" * 68]
    listed = [row for row in records if show_all or row["rating"] != PORTABLE]
    for row in sorted(listed, key=lambda r: (-RANK[r["rating"]], r["source"],
                                             r["path"])):
        lines.append("%-18s %s / %s / %s"
                     % (row["rating"], row["source"], row["where"],
                        row["name"] or row["path"]))
        if row["layer"]:
            lines.append("    layer: %s" % row["layer"])
        for reason in row["reasons"]:
            lines.append("    %s %s" % (reason["name"], reason["why"]))
    if listed:
        lines.append("-" * 68)
    for name in (ESRI_ONLY, WITH_WORK, PORTABLE):
        lines.append("  %-20s %d" % (name, summary["by_rating"][name]))
    if summary["by_where"]:
        lines.append("  by location:         " + ", ".join(
            "%s %d" % (key, summary["by_where"][key])
            for key in sorted(summary["by_where"])))
    if summary["by_reason"]:
        lines.append("  what blocks a move:")
        for entry in sorted(summary["by_reason"].values(),
                            key=lambda e: (-RANK[e["rating"]], -e["count"],
                                           e["name"].lower())):
            lines.append("    %3d  %-28s %s"
                         % (entry["count"], entry["name"], entry["why"]))
    return lines


# ------------------------------------------------------------------ self-test

# r-strings throughout. These are JSON documents, so every \n and \" in them is
# an escape JSON has to see, and a plain string would eat both and hand the
# parser something else. Accented characters are written as unicode escapes for
# the same reason: this file stays pure ASCII, and json.loads and the Python
# parser both turn the escape back into the character it names.

# A web map with Arcade in all four places it can hide, plus the three shapes
# that must NOT be collected: the legacy [FIELD] label syntax, a form element
# that names an expression rather than carrying one, and an empty one.
WEBMAP_JSON = r"""
{
 "operationalLayers": [
  {
   "id": "Parcels_1234",
   "title": "Parcels",
   "url": "https://services1.arcgis.com/EXAMPLE/arcgis/rest/services/Parcels/FeatureServer/0",
   "popupInfo": {
    "title": "{PARCELID}",
    "expressionInfos": [
     {"name": "expr0", "title": "Owner mailing", "returnType": "string",
      "expression": "$feature.OWNER + \" \" + $feature.MAIL1"},
     {"name": "expr1", "title": "Neighbours", "returnType": "number",
      "expression": "var parcels = FeatureSetByName($map, \"Parcels\");\nreturn Count(Intersects(parcels, $feature));"},
     {"name": "expr2", "title": "Acres", "returnType": "number",
      "expression": "Round(AreaGeodetic($feature, \"acres\"), 2)"},
     {"name": "expr3", "title": "Migration note", "returnType": "string",
      "expression": "// FeatureSetByPortalItem was here until 2024\nreturn \"read FeatureSetByName in the docs\";"}
    ]
   },
   "layerDefinition": {
    "drawingInfo": {
     "renderer": {
      "type": "uniqueValue",
      "valueExpression": "When($feature.STATUS == 1, \"open\", \"closed\")",
      "valueExpressionTitle": "Status band",
      "visualVariables": [
       {"type": "sizeInfo", "valueExpression": "$feature.HEIGHT * 3"}
      ]
     }
    }
   }
  },
  {
   "id": "Hydrants_5678",
   "title": "Hydrants",
   "layerDefinition": {
    "drawingInfo": {
     "labelingInfo": [
      {
       "labelExpression": "[HYDRANTID]",
       "labelExpressionInfo": {
        "expression": "$feature.HYDRANTID + \" (\" + DomainName($feature, \"STATUS\") + \")\""
       }
      }
     ]
    }
   },
   "formInfo": {
    "title": "Hydrant form",
    "expressionInfos": [
     {"name": "expr0", "title": "Inspector", "returnType": "string",
      "expression": "GetUser($map, \"\").username"},
     {"name": "expr1", "title": "Flow check", "returnType": "boolean",
      "expression": "$feature.FLOW > 0"}
    ],
    "formElements": [
     {"type": "field", "fieldName": "FLOW", "label": "Flow",
      "valueExpression": "expr0",
      "visibilityExpression": "expr1"}
    ]
   }
  }
 ],
 "tables": [
  {
   "id": "Inspections_9",
   "title": "Inspections",
   "popupInfo": {
    "expressionInfos": [
     {"name": "expr0", "title": "R\u00e9sum\u00e9", "returnType": "string",
      "expression": "\"R\u00e9sum\u00e9 \" + $feature.NOTE"},
     {"name": "expr1", "title": "Blank", "returnType": "string",
      "expression": "   "},
     {"name": "expr2", "title": "Not a string", "returnType": "number",
      "expression": 42}
    ]
   }
  }
 ],
 "baseMap": {"title": "Topographic", "baseMapLayers": [{"id": "topo"}]}
}
"""

# A dashboard. The Arcade data expression sits under a widget's dataSource,
# which is why the collector walks for the key rather than reading a fixed path.
DASHBOARD_JSON = r"""
{
 "widgets": [
  {
   "id": "indicator1", "name": "Open permits", "type": "indicatorWidget",
   "datasets": [
    {"type": "serviceDataset", "name": "main",
     "dataSource": {"type": "featureServiceDataSource", "itemId": "ab12cd34"}},
    {"type": "arcadeDataset", "name": "expr",
     "dataSource": {"type": "arcadeDataSource",
      "expression": "var fs = FeatureSetByPortalItem(Portal(\"https://county.maps.arcgis.com\"), \"ab12cd34\", 0);\nreturn Count(fs);"}}
   ]
  },
  {
   "id": "list1", "name": "Recent inspections", "type": "listWidget",
   "datasets": [
    {"dataSource": {"expression": "Text($feature.INSPECTED, \"Y-MM-DD\") + \" \" + $feature.INSPECTOR"}}
   ]
  },
  {"id": "map1", "name": "Overview", "type": "mapWidget", "itemId": "cd34ab12"}
 ]
}
"""

# A form on its own, as ArcGIS Survey123 and the field maps form editor write it.
FORM_JSON = r"""
{
 "formInfo": {
  "title": "Inspection form",
  "expressionInfos": [
   {"name": "expr0", "title": "Default owner", "returnType": "string",
    "expression": "$feature.OWNER"},
   {"name": "expr1", "title": "Must sit in a zone", "returnType": "boolean",
    "expression": "Intersects($feature, FeatureSetByName($datastore, \"Zones\"))"}
  ],
  "formElements": [
   {"type": "field", "fieldName": "OWNER", "label": "Owner",
    "valueExpression": "expr0"}
  ]
 }
}
"""

# Real gdbxray --json output, from a real File Geodatabase built with arcpy for
# this self-test. Six attribute rules on two feature classes, byte for byte as
# gdbxray printed them, with the field lists and the domain and relationship
# sections cut out because nothing here reads them.
#
# The GUID inside the FeatureSetByName calls is not a typo. It is what ArcGIS
# stored: the rule was written against the name "Hydrants" and the geodatabase
# rewrote it to the destination class's UUID. An expression like that cannot
# even be read without the geodatabase it came from, which is the point.
RULES_JSON = r"""
{
 "counts": {
  "attachments": 0, "datasets": 2, "domains": 0, "items": 4,
  "relationships": 0, "rules": 6, "subtypes": 0, "unrecognised": 2
 },
 "datasets": [
  {
   "item": "dataset",
   "name": "WaterLines",
   "uuid": "{0738DA4E-9876-4160-BB17-FAF9A69FFC02}",
   "rules": [
    {
     "description": "Condition from diameter", "enabled": true,
     "expression": "IIf($feature.DIAMETER >= 12, \"MAIN\", \"LATERAL\")",
     "field": "CONDITION", "name": "CalcCondition",
     "on_delete": false, "on_insert": true, "on_update": true,
     "rule_type": "calculation"
    },
    {
     "description": "How many hydrants hang off this line", "enabled": true,
     "expression": "var h = FeatureSetByName($datastore, \"{18C465CB-4C24-4716-957B-E8093E2102CD}\", [\"LINEID\"], false);\nreturn Count(h);",
     "field": "HYDCOUNT", "name": "CountHydrants",
     "on_delete": false, "on_insert": true, "on_update": true,
     "rule_type": "calculation"
    },
    {
     "description": "Diameter must be > 0", "enabled": true,
     "expression": "$feature.DIAMETER > 0",
     "field": "", "name": "DiameterPositive",
     "on_delete": false, "on_insert": true, "on_update": true,
     "rule_type": "constraint"
    },
    {
     "description": "one short line, still not portable", "enabled": true,
     "expression": "Text(Count(FeatureSetByName($datastore,\"{18C465CB-4C24-4716-957B-E8093E2102CD}\")))",
     "field": "OWNERNAME", "name": "OwnerName",
     "on_delete": false, "on_insert": true, "on_update": false,
     "rule_type": "calculation"
    }
   ]
  },
  {
   "item": "dataset",
   "name": "Hydrants",
   "uuid": "{18C465CB-4C24-4716-957B-E8093E2102CD}",
   "rules": [
    {
     "description": "geometry predicate", "enabled": true,
     "expression": "Intersects($feature, Buffer(Geometry($feature), 5, \"feet\"))",
     "field": "", "name": "SnapCheck",
     "on_delete": false, "on_insert": true, "on_update": true,
     "rule_type": "constraint"
    },
    {
     "description": "names appear only in a comment and a string",
     "enabled": true,
     "expression": "// FeatureSetByName was removed here, see ticket 8812\nvar note = \"call FeatureSetByPortalItem instead\";\nreturn Upper($feature.LINEID) + note;",
     "field": "LINEID", "name": "CommentOnly",
     "on_delete": false, "on_insert": true, "on_update": false,
     "rule_type": "calculation"
    }
   ]
  }
 ],
 "source": "arcade.gdb"
}
"""

# A long expression that is entirely portable, built here rather than pasted so
# that its length is obviously the only unusual thing about it.
LONG_PORTABLE = "\n".join(
    ["var total = 0;"] +
    ["total = total + $feature.FEE_%02d * 1.075;" % n for n in range(1, 31)] +
    ["return Round(total, 2);"])

# The twin of it: one line, one call, and it cannot leave.
SHORT_UNPORTABLE = 'FeatureSetByName($datastore, "Hydrants")'


def _harness(quiet=False):
    """The pass and fail counter, as a factory so the self-test can test it.

    A harness that cannot record a failure turns every run green, including the
    runs where the tool is broken, so this one is built by a function that the
    self-test calls a second time to prove it counts.
    """
    passed = [0]
    failed = []

    def check(condition, label):
        if condition:
            passed[0] += 1
            if not quiet:
                print("PASS  %s" % label)
        else:
            failed.append(label)
            if not quiet:
                print("FAIL  %s" % label)

    def raises(function, label, exception=ValueError):
        """Assert function raises, and hand the message back to assert on."""
        try:
            function()
        except exception as exc:
            check(True, label)
            return str(exc)
        except Exception as exc:
            check(False, "%s (wrong exception %r)" % (label, exc))
        else:
            check(False, "%s (no error raised)" % label)
        return ""

    return check, raises, passed, failed


class _AsciiOnlyStream(object):
    """A stream that refuses anything but ASCII, the way a redirected Windows
    console does. Linux never behaves like this, so the branch that survives it
    has to be driven by a stand-in or it is only ever tested on one platform."""

    encoding = "ascii"

    def __init__(self):
        self.text = ""

    def write(self, text):
        text.encode("ascii")
        self.text += text


def self_test():
    """Assertions over the rater, the collectors, the CSV, the CLI and a portal.

    The rater and the collectors are pure, so most of this is arithmetic over
    strings. The last two blocks are not: one writes a CSV into a temporary
    directory, and one starts an HTTP server on the loopback address and reads
    an item through the same urllib code path a real portal gets. No credential,
    no network and no geodatabase is needed for any of it.
    """
    import shutil
    import tempfile
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from urllib.parse import parse_qs, urlsplit

    check, raises, passed, failed = _harness()

    def capture(function):
        """(what function returned, everything it printed to either stream)."""
        buffer = io.StringIO()
        real_out, real_err = sys.stdout, sys.stderr
        sys.stdout = sys.stderr = buffer
        try:
            result = function()
        finally:
            sys.stdout, sys.stderr = real_out, real_err
        return result, buffer.getvalue()

    print("arcadecheck self-test: no portal, no geodatabase, no credentials")
    print("-" * 68)

    # ---- comments and string literals are not code
    check(strip_noise("a // b").rstrip() == "a",
          "a line comment is blanked out")
    check(len(strip_noise("a // b")) == len("a // b"),
          "and the blanked text is the same length as what came in")
    check(strip_noise("a // b\nc") == "a     \nc",
          "the newline that ends a line comment survives it")
    check(strip_noise("a /* b */ c") == "a         c",
          "a block comment is blanked out")
    check(strip_noise("a /* b\nc */ d") == "a     \n     d",
          "a block comment keeps the lines it spans, so line numbers still line up")
    check(strip_noise('x = "FeatureSetByName"') == "x = " + " " * 18,
          "a double quoted literal is blanked out")
    check(strip_noise("x = 'FeatureSetByName'") == "x = " + " " * 18,
          "a single quoted literal is blanked out too")
    check(strip_noise('"a\\"b" + c').endswith("+ c"),
          "an escaped quote does not end the literal early")
    check(strip_noise('"unterminated + c') == " " * len('"unterminated + c'),
          "an unterminated literal swallows the rest, which is what Arcade does")
    check(strip_noise("/* unterminated").strip() == "",
          "an unterminated block comment swallows the rest for the same reason")
    check(strip_noise("") == "" and strip_noise(None) == "",
          "an empty or null expression strips to the empty string")
    check(strip_noise("$feature.A / $feature.B") == "$feature.A / $feature.B",
          "a division is not the start of a comment  <-- pinned defect")
    check(strip_noise("a /") == "a /",
          "a trailing slash at the end of the text is not a comment")
    check(strip_noise('"/* not a comment */"').strip() == "",
          "a comment opener inside a literal opens nothing")
    check(strip_noise("// \"not a literal").strip() == "",
          "a quote inside a line comment opens nothing")
    check(strip_noise('"it\'s here"').strip() == "",
          "a single quote inside a double quoted literal does not end it")
    check(strip_noise("'say \"hi\"'").strip() == "",
          "and the other way round")
    check(strip_noise('x = "a\nb" + c').count("\n") == 1,
          "a newline inside a literal is kept")
    check(strip_noise('"a\\\nb"').count("\n") == 1,
          "an escaped newline inside a literal is kept as a line")
    check(strip_noise("/*a*/b") == "     b",
          "the closing */ is blanked along with the comment")
    check(strip_noise("a/*b*/c/*d*/e") == "a     c     e",
          "two block comments in one line are both blanked")

    # ---- what an expression calls
    check(find_calls("Count(x)") == ["Count"],
          "a call is found by its name and bracket")
    check(find_calls("Count (x)") == ["Count"],
          "a space before the bracket is still a call")
    check(find_calls("Count\n(x)") == ["Count"],
          "a newline before the bracket is still a call")
    check(find_calls("$feature.Buffer(3)") == ["$feature"],
          "a member called after a dot is not a call to a function of that "
          "name  <-- pinned defect")
    check(find_calls("Buffer($feature.Shape, 3)") == ["Buffer", "$feature"],
          "but the same name at the start of the expression is")
    check(find_calls("$map") == ["$map"],
          "a profile variable is found without a bracket")
    check(find_calls("$feature.NAME") == ["$feature"],
          "a field read reports the variable, not the field")
    check(find_calls("x$map") == [],
          "a dollar inside a word is not a profile variable")
    check(find_calls("if (x) { return Count(y) }") == ["Count"],
          "the if of an if statement is not a call  <-- pinned defect")
    check(find_calls("for (var i in x) { }") == [],
          "nor the for of a loop")
    check(find_calls("return (1 + 2)") == [],
          "nor a return with a bracketed value")
    check(find_calls("Count(x) + count(y)") == ["Count"],
          "the same name in two cases is one call, spelled as it was written first")
    check(find_calls("Sum(Count(x), Area(y))") == ["Sum", "Count", "Area"],
          "nested calls are all found, outermost first")
    check(find_calls("b(1) + a(2)") == ["b", "a"],
          "calls come back in the order written, not sorted")
    check(find_calls("FeatureSetByName($map, 'x')") == ["FeatureSetByName", "$map"],
          "a call and the variable inside it are both found")
    check(find_calls("") == [], "nothing is found in an empty expression")
    check(find_calls("42 + 7") == [], "arithmetic calls nothing")
    check(find_calls("Count_2(x)") == ["Count_2"],
          "a digit and an underscore are part of a name")
    check(find_calls("_private(x)") == ["_private"],
          "a name may start with an underscore")
    check(find_calls("2(x)") == [],
          "a number in front of a bracket is not a call")
    check(find_calls("$FEATURE") == ["$FEATURE"],
          "a profile variable is reported as written")
    check(find_calls("Count(Count(x))") == ["Count"],
          "one name called twice is reported once")
    check(find_calls("Portal('https://x')") == ["Portal"],
          "a url argument does not confuse the name in front of it")
    check(find_calls("$feature.A + $feature.B") == ["$feature"],
          "one variable read twice is reported once")

    # ---- the rating rules, each with a twin that must NOT match
    esri = [
        ("FeatureSetByName($datastore, 'Hydrants')", "FeatureSetByName"),
        ("FeatureSetByPortalItem(Portal('https://x'), 'ab12', 0)",
         "FeatureSetByPortalItem"),
        ("FeatureSetByRelationshipName($feature, 'Inspections')",
         "FeatureSetByRelationshipName"),
        ("FeatureSetById($map, '0')", "FeatureSetById"),
        ("Portal('https://county.maps.arcgis.com')", "Portal"),
        ("GetUser($map).username", "GetUser"),
        ("$map", "$map"),
        ("$datastore", "$datastore"),
        ("$layer", "$layer"),
        ("$editcontext.editType", "$editcontext"),
        ("$originalfeature.NAME", "$originalfeature"),
        ("Count($featureset)", "$featureset"),
        ("Count($aggregatedfeatures)", "$aggregatedfeatures"),
    ]
    for expression, name in esri:
        rated = rate(expression)
        check(rated["rating"] == ESRI_ONLY,
              "%s makes an expression esri only" % name)
        check(any(r["name"].lower() == name.lower() for r in rated["reasons"]),
              "and %s is named as the reason" % name)

    # The twins: the same name written where it cannot run.
    for expression, name in esri:
        commented = "// %s\nreturn 1;" % expression
        check(rate(commented)["rating"] == PORTABLE,
              "%s inside a comment does not count  <-- pinned defect" % name)
        quoted = 'return "%s";' % expression.replace('"', "'")
        check(rate(quoted)["rating"] == PORTABLE,
              "%s inside a string literal does not count  <-- pinned defect"
              % name)

    work = [
        ("Intersects($feature, other)", "Intersects", "shapely"),
        ("Buffer($feature, 10, 'feet')", "Buffer", "shapely"),
        ("Area($feature)", "Area", "shapely"),
        ("AreaGeodetic($feature, 'acres')", "AreaGeodetic", "geographiclib"),
        ("LengthGeodetic($feature, 'feet')", "LengthGeodetic", "geographiclib"),
        ("DomainName($feature, 'STATUS')", "DomainName", "table"),
        ("SubtypeName($feature)", "SubtypeName", "table"),
        ("Attachments($feature)", "Attachments", "blob store"),
        ("Schema($feature)", "Schema", "schema"),
    ]
    for expression, name, word in work:
        rated = rate(expression)
        check(rated["rating"] == WITH_WORK,
              "%s makes an expression portable with work" % name)
        check(any(word in r["why"] for r in rated["reasons"]),
              "and the reason for %s names what replaces it" % name)

    check(rate("$feature.A * 1.075")["rating"] == PORTABLE,
          "arithmetic over the feature's own fields is portable")
    check(rate("$feature.A * 1.075")["reasons"] == [],
          "and it is given no reason to be anything else")
    check(rate("IIf($feature.A > 2, 'big', 'small')")["rating"] == PORTABLE,
          "IIf is portable, because every language has one")
    check(rate("Count(Filter($feature.ITEMS, 'x'))")["rating"] == PORTABLE,
          "counting and filtering an array is portable")
    check(rate("Text(Now(), 'Y-MM-DD')")["rating"] == PORTABLE,
          "formatting a date is portable")
    check(rate("Upper($feature.NAME) + Concatenate(['a', 'b'])")["rating"]
          == PORTABLE, "string functions are portable")

    mixed = rate("var f = FeatureSetByName($map, 'X');\n"
                 "return Area(Intersection(f, $feature));")
    check(mixed["rating"] == ESRI_ONLY,
          "an expression that calls both is rated by the worst of the two")
    check(len(mixed["reasons"]) == 4,
          "and every blocking call is listed, not only the worst one")
    check([r["name"] for r in mixed["reasons"]][:2]
          == ["$map", "FeatureSetByName"],
          "reasons are sorted worst first, then by name")
    check(mixed["reasons"][-1]["rating"] == WITH_WORK,
          "so the geometry work lands at the end")
    check(mixed["calls"][0] == "FeatureSetByName",
          "the calls column stays in the order the expression wrote them")

    # ---- the pinned defect: length is not portability
    short = rate(SHORT_UNPORTABLE)
    long_one = rate(LONG_PORTABLE)
    check(short["rating"] == ESRI_ONLY,
          "a ONE LINE expression calling FeatureSetByName is esri only  "
          "<-- pinned defect")
    check(long_one["rating"] == PORTABLE,
          "a THIRTY LINE arithmetic expression is portable  <-- pinned defect")
    check(long_one["chars"] > short["chars"] * 20,
          "and the portable one is more than twenty times longer than the "
          "unportable one  <-- pinned defect")
    check(short["lines"] == 1 and long_one["lines"] == 32,
          "the line counts are reported, so the csv can still show the size "
          "of the job")
    check(rate("")["rating"] == PORTABLE and rate("")["chars"] == 0,
          "an empty expression rates portable with nothing in it")
    check(rate("")["lines"] == 0,
          "and an empty expression is zero lines, not one")
    check(rate(None)["rating"] == PORTABLE,
          "a null expression rates portable instead of raising")
    check(rate(42)["rating"] == PORTABLE and rate(42)["chars"] == 0,
          "a number where an expression should be is not fed to the regex")
    accented = rate('return "R\u00e9sum\u00e9 " + $feature.NOTE;')
    check(accented["rating"] == PORTABLE,
          "non-ascii text inside an expression is portable")
    check(accented["chars"] == 33,
          "and its length is counted in characters, not bytes")
    check(rate('FeatureSetByName($datastore, "Caf\u00e9")')["rating"]
          == ESRI_ONLY,
          "a non-ascii argument does not hide the call around it")

    # ---- reading a document
    sample = {"a": [{"b": "x"}, 2], "c": {"d": None}}
    paths = [item[0] for item in walk(sample)]
    check(paths == ["a[0].b", "a[1]", "c.d"],
          "every scalar in a document comes back with its path")
    check([item[1] for item in walk(sample)] == ["b", "", "d"],
          "a scalar inside a list has no key of its own")
    check(list(walk(sample))[0][3] is sample["a"][0],
          "and each one carries the object that holds it")
    check(list(walk({})) == [] and list(walk([])) == [],
          "an empty document holds nothing")
    check(list(walk("just a string")) == [],
          "a bare string is not a document and yields nothing")
    check(lookup(sample, "a[0].b") == "x", "a path reads back the value it named")
    check(lookup(sample, "c.d") is None, "a null value reads back as null")
    check(lookup(sample, "a[9]") is None, "an index past the end reads as null")
    check(lookup(sample, "a[2]") is None,
          "including the one immediately past it, where a list of two ends  "
          "<-- pinned defect")
    check(lookup(sample, "a[1]") == 2,
          "while the last index there is reads the value it holds")
    check(lookup(sample, "nope") is None, "a missing key reads as null")
    check(lookup(sample, "a.b") is None,
          "a key asked for on a list reads as null rather than raising")
    check(lookup(sample, "a[1].b") is None,
          "walking into a number reads as null rather than raising")
    check(lookup(sample, "") is sample, "the empty path is the document itself")
    check(detect(json.loads(WEBMAP_JSON)) == "webmap",
          "a document with operational layers is a web map")
    check(detect(json.loads(DASHBOARD_JSON)) == "dashboard",
          "a document with widgets is a dashboard")
    check(detect(json.loads(FORM_JSON)) == "form",
          "a document with a formInfo is a form")
    check(detect(json.loads(RULES_JSON)) == "rules",
          "a gdbxray report is recognised by its counts and datasets")
    check(detect({"datasets": [], "counts": {"items": 1}}) == "json",
          "a counts block with no rule count is not a gdbxray report")
    check(detect({"counts": {"rules": 1}, "datasets": [],
                  "operationalLayers": []}) == "rules",
          "a document carrying both shapes is read as the gdbxray report, "
          "because its rules are not a web map's layers  <-- pinned defect")
    check(detect({}) == "json" and detect([]) == "json",
          "anything else is just json")
    check(detect(None) == "json", "and so is nothing at all")
    check(is_expression("$feature.A") is True, "arcade source is an expression")
    check(is_expression("expr0") is False,
          "a bare identifier is the NAME of an expression, not one  "
          "<-- pinned defect")
    check(is_expression("Count") is False,
          "even when the name is spelled like a function")
    check(is_expression("Count(x)") is True,
          "adding the brackets makes it code again")
    check(is_expression("   ") is False, "whitespace holds nothing to port")
    check(is_expression("") is False, "nor does an empty string")
    check(is_expression(None) is False and is_expression(42) is False,
          "nor does a null or a number")
    check(where_from("operationalLayers[0].popupInfo.expressionInfos[0]"
                     ".expression", "webmap") == "popup",
          "an expression under popupInfo is a popup expression")
    check(where_from("operationalLayers[1].layerDefinition.drawingInfo"
                     ".labelingInfo[0].labelExpressionInfo.expression",
                     "webmap") == "label",
          "a label is a label, although its path runs through drawingInfo  "
          "<-- pinned defect")
    check(where_from("operationalLayers[0].layerDefinition.drawingInfo"
                     ".renderer.valueExpression", "webmap") == "renderer",
          "a renderer expression is a renderer expression")
    check(where_from("operationalLayers[0].layerDefinition.drawingInfo"
                     ".renderer.visualVariables[0].valueExpression", "webmap")
          == "renderer", "and so is one on a visual variable")
    check(where_from("formInfo.expressionInfos[0].expression", "form")
          == "form", "an expression under formInfo is a form expression")
    check(where_from("widgets[0].datasets[1].dataSource.expression",
                     "dashboard") == "dashboard",
          "an expression under a widget is a dashboard expression")
    check(where_from("somewhere.else", "webmap") == "expression",
          "an unrecognised path in a web map is just an expression")
    check(where_from("somewhere.else", "dashboard") == "dashboard",
          "but in a dashboard it still belongs to the dashboard")
    check(name_from({"title": "Acres", "name": "expr2"}, "expression")
          == "Acres", "an expression is named by its title first")
    check(name_from({"name": "expr2"}, "expression") == "expr2",
          "and by its name when there is no title")
    check(name_from({"title": "  "}, "expression") == "expression",
          "a blank title falls through to the key")
    check(name_from(None, "valueExpression") == "valueExpression",
          "and so does no object at all")
    check(container_label({"layers": [{"popupInfo": {}}]},
                          "layers[0].popupInfo.expression") == "",
          "a layer with no title of any kind leaves the layer column empty")
    check(container_label({"layers": ["a", "b"]}, "layers[0]") == "",
          "and so does a list of strings, rather than raising")
    check(container_label({"a": {"b": "c"}}, "a.b") == "",
          "a path with nothing indexed on it has no container")

    # ---- a web map
    webmap = json.loads(WEBMAP_JSON)
    rows = collect(webmap, "webmap.json")
    by_name = dict((row["name"], row) for row in rows)
    check(len(rows) == 10,
          "ten expressions are collected from the web map")
    check(len(by_name) == 10, "and every one of them is a separate row")
    check(all(row["source"] == "webmap.json" for row in rows),
          "each row says which document it came from")
    check(all(row["document"] == "webmap" for row in rows),
          "and that the document was a web map")
    counted = {}
    for row in rows:
        counted[row["where"]] = counted.get(row["where"], 0) + 1
    check(counted == {"popup": 5, "renderer": 2, "label": 1, "form": 2},
          "the expressions are split across popups, renderers, labels and a form")
    check(by_name["Owner mailing"]["rating"] == PORTABLE,
          "a popup expression over the feature's own fields is portable")
    check(by_name["Neighbours"]["rating"] == ESRI_ONLY,
          "a popup expression reading another layer is esri only")
    check(by_name["Acres"]["rating"] == WITH_WORK,
          "a geodetic area is portable with work")
    check(by_name["Migration note"]["rating"] == PORTABLE,
          "an expression naming two esri functions in a comment and a string "
          "is portable  <-- pinned defect")
    check(by_name["Migration note"]["calls"] == [],
          "and it is recorded as calling nothing at all")
    check(by_name["Inspector"]["rating"] == ESRI_ONLY,
          "a form expression reading the portal user is esri only")
    check(by_name["Flow check"]["rating"] == PORTABLE,
          "a form constraint over one field is portable")
    check(by_name["R\u00e9sum\u00e9"]["rating"] == PORTABLE,
          "a popup expression with non-ascii text is portable")
    check(by_name["Owner mailing"]["layer"] == "Parcels",
          "a row names the layer the expression hangs off")
    check(by_name["Inspector"]["layer"] == "Hydrants",
          "including one buried in that layer's form")
    check(by_name["R\u00e9sum\u00e9"]["layer"] == "Inspections",
          "and one on a table rather than a layer")
    label_rows = [row for row in rows if row["where"] == "label"]
    check(len(label_rows) == 1,
          "the legacy [FIELD] label syntax is NOT collected as arcade  "
          "<-- pinned defect")
    check(label_rows[0]["rating"] == WITH_WORK,
          "the arcade label beside it is collected, and needs a domain lookup")
    check(label_rows[0]["path"].endswith("labelExpressionInfo.expression"),
          "and its path says where to find it again")
    check(not any(row["expression"] == "expr0" for row in rows),
          "a form element that names an expression is not collected as one  "
          "<-- pinned defect")
    check(not any(row["path"].endswith("visibilityExpression") for row in rows),
          "nor is a key that was never arcade")
    check(not any(row["expression"] == "   " for row in rows),
          "an empty expression is not a row")
    check(not any(row["expression"] == 42 for row in rows),
          "nor is a number sitting where an expression should be")
    check(by_name["Neighbours"]["path"]
          == "operationalLayers[0].popupInfo.expressionInfos[1].expression",
          "the path of a row is the json path it was read from")
    check(lookup(webmap, by_name["Neighbours"]["path"])
          == by_name["Neighbours"]["expression"],
          "and that path reads the same expression back out of the document")
    check(collect({}, "empty.json") == [],
          "an empty document holds no expressions")
    loose = collect({"expression": "$feature.A", "other": "Count(x)"}, "x")
    check(len(loose) == 1 and loose[0]["where"] == "expression",
          "an arcade key in a document of no known shape is still collected, "
          "and a key that was never arcade beside it is not")

    # ---- a dashboard
    dashboard = collect(json.loads(DASHBOARD_JSON), "dashboard.json")
    check(len(dashboard) == 2, "two expressions are collected from the dashboard")
    check(all(row["where"] == "dashboard" for row in dashboard),
          "both are recorded as dashboard expressions")
    check(dashboard[0]["layer"] == "Open permits",
          "a dashboard row names the widget it came from")
    check(dashboard[0]["rating"] == ESRI_ONLY,
          "a data expression reading a portal item is esri only")
    check([r["name"] for r in dashboard[0]["reasons"]]
          == ["FeatureSetByPortalItem", "Portal"],
          "and both of the calls that make it so are named")
    check(dashboard[1]["rating"] == PORTABLE,
          "a list expression over the feature's own fields is portable")
    check(dashboard[1]["layer"] == "Recent inspections",
          "and it is attributed to its own widget")
    check(dashboard[0]["path"]
          == "widgets[0].datasets[1].dataSource.expression",
          "the path reaches into the widget's dataset")
    check(dashboard[0]["document"] == "dashboard",
          "the document type is carried onto every row")

    # ---- a form
    form = collect(json.loads(FORM_JSON), "form.json")
    check(len(form) == 2, "two expressions are collected from the form")
    check(all(row["where"] == "form" for row in form),
          "both are recorded as form expressions")
    check(form[0]["name"] == "Default owner" and form[0]["rating"] == PORTABLE,
          "the calculation over one field is portable")
    check(form[1]["rating"] == ESRI_ONLY,
          "the constraint that reads another layer is not")
    check(form[0]["layer"] == "",
          "a form expression has no layer, rather than repeating its own "
          "title  <-- pinned defect")
    check(not any(row["expression"] == "expr0" for row in form),
          "the element that points at the calculation by name is not a "
          "second row")

    # ---- attribute rules, out of real gdbxray output
    report = json.loads(RULES_JSON)
    rules = collect(report, "rules.json")
    rule_by = dict((row["name"], row) for row in rules)
    check(len(rules) == 6,
          "all six attribute rules in the geodatabase are collected")
    check(report["counts"]["rules"] == len(rules),
          "which is the rule count gdbxray reported for that geodatabase")
    check(all(row["where"] == "rule" for row in rules),
          "each one is recorded as an attribute rule")
    check(all(row["document"] == "rules" for row in rules),
          "and as having come from a gdbxray report")
    check(rule_by["CalcCondition"]["layer"] == "WaterLines",
          "a rule carries the feature class it is attached to")
    check(rule_by["SnapCheck"]["layer"] == "Hydrants",
          "including the rules on the second feature class")
    check(rule_by["CalcCondition"]["rating"] == PORTABLE,
          "an IIf over the row's own fields is portable")
    check(rule_by["DiameterPositive"]["rating"] == PORTABLE,
          "so is a constraint comparing one field to a number")
    check(rule_by["CountHydrants"]["rating"] == ESRI_ONLY,
          "a rule calling FeatureSetByName over $datastore is esri only")
    check([r["name"] for r in rule_by["CountHydrants"]["reasons"]]
          == ["$datastore", "FeatureSetByName"],
          "and both halves of that call are named as reasons")
    check(rule_by["SnapCheck"]["rating"] == WITH_WORK,
          "a geometry constraint is portable with work")
    check(len(rule_by["SnapCheck"]["reasons"]) == 3,
          "with one reason for each geometry call it makes")
    check(rule_by["CommentOnly"]["rating"] == PORTABLE,
          "a rule naming two esri functions in a comment and a string is "
          "portable  <-- pinned defect")
    check(rule_by["OwnerName"]["rating"] == ESRI_ONLY,
          "a rule that is ONE line is still esri only  <-- pinned defect")
    check(rule_by["OwnerName"]["lines"] == 1
          and rule_by["CommentOnly"]["lines"] == 3,
          "even though the portable rule beside it is three lines long  "
          "<-- pinned defect")
    check(rule_by["OwnerName"]["chars"] < rule_by["CommentOnly"]["chars"],
          "and shorter than it in characters too  <-- pinned defect")
    check("{18C465CB-4C24-4716-957B-E8093E2102CD}"
          in rule_by["OwnerName"]["expression"],
          "the layer name inside the rule is the guid the geodatabase stored, "
          "not the name it was written with")
    check(rule_by["CalcCondition"]["path"]
          == "datasets[0].rules[0].expression",
          "a rule's path points back into the gdbxray report")
    check(lookup(report, rule_by["SnapCheck"]["path"])
          == rule_by["SnapCheck"]["expression"],
          "and reads the same expression back out of it")
    check(collect_rules({"datasets": "not a list"}) == [],
          "a report with no dataset list yields nothing rather than raising")
    check(collect_rules({"datasets": [None, {"rules": None},
                                     {"rules": [None, {"expression": ""}]}]})
          == [],
          "and neither a broken dataset nor a broken rule stops the read")

    # ---- the summary
    summary = summarise(rows)
    check(summary["total"] == 10, "the summary counts every expression")
    check(summary["by_rating"][ESRI_ONLY] == 2,
          "two of the web map's expressions cannot leave arcgis")
    check(summary["by_rating"][WITH_WORK] == 2,
          "two more can leave with work")
    check(summary["by_rating"][PORTABLE] == 6,
          "and six move as they are")
    check(sum(summary["by_rating"].values()) == summary["total"],
          "every expression is counted exactly once")
    check(summary["worst"] == ESRI_ONLY,
          "the worst rating in the document is reported on its own")
    check(summary["sources"] == ["webmap.json"],
          "the summary lists the documents it read")
    check(summary["chars"] == 455,
          "and the total size of the job, which is 455 characters of arcade")
    check(summary["chars"] == sum(row["chars"] for row in rows),
          "the same number the rows add up to on their own")
    check(summary["by_reason"]["$map"]["count"] == 2,
          "a reason is counted once for each expression that hits it")
    check(summary["by_reason"]["featuresetbyname"]["count"] == 1,
          "and reasons are keyed case insensitively")
    twice = summarise([record("x", "json", "expression", "", "n", "p",
                              "Count(FeatureSetByName($map, 'a')) + "
                              "Count(FeatureSetByName($map, 'b'))")])
    check(twice["by_reason"]["featuresetbyname"]["count"] == 1,
          "one expression calling the same function twice is one expression "
          "to rewrite, not two  <-- pinned defect")
    empty_summary = summarise([])
    check(empty_summary["total"] == 0 and empty_summary["worst"] == PORTABLE,
          "a document with no arcade in it is not a problem")
    check(empty_summary["by_rating"] == {PORTABLE: 0, WITH_WORK: 0,
                                         ESRI_ONLY: 0},
          "and all three counts are reported as zero rather than missing")
    both = summarise(rows + rules)
    check(both["total"] == 16 and len(both["sources"]) == 2,
          "two documents add up into one summary")
    check(both["by_rating"][ESRI_ONLY] == 4,
          "with the esri only expressions of both in the total")

    # ---- the report on screen
    text = "\n".join(render(rows, summary))
    check("10 expression(s) in 1 document(s)" in text,
          "the report opens with what it read")
    check(text.count("ESRI ONLY") == 3,
          "the two esri only expressions are listed, and counted once more in "
          "the totals underneath")
    check("Owner mailing" not in text,
          "a portable expression is not listed, because there is nothing to do")
    check("Owner mailing" in "\n".join(render(rows, summary, show_all=True)),
          "unless the report was asked for all of them")
    check(text.index("ESRI ONLY") < text.index("PORTABLE WITH WORK"),
          "what cannot move at all is printed first")
    check("layer: Parcels" in text, "each listed row names its layer")
    check("what blocks a move:" in text,
          "the report ends with the calls that block the move")
    check(text.rstrip().endswith("a geometry operation; shapely and GEOS do "
                                 "the same work under another name"),
          "and the last line of it is the least bad reason")
    formless = "\n".join(render(form, summarise(form)))
    check("Must sit in a zone" in formless and "layer:" not in formless,
          "a row with no layer prints no empty layer line")
    quiet = "\n".join(render([], summarise([])))
    check("0 expression(s) in 0 document(s)" in quiet,
          "a document with no arcade still gets a report")
    check("what blocks a move" not in quiet,
          "and that report has nothing blocking a move")

    # ---- the csv
    line = csv_row(by_name["Neighbours"])
    check(len(line) == len(COLUMNS),
          "a csv row has one cell for every column")
    check(line[COLUMNS.index("rating")] == ESRI_ONLY,
          "the rating is written as it is printed")
    check(line[COLUMNS.index("calls")]
          == "FeatureSetByName, $map, Count, Intersects, $feature",
          "the calls cell lists what the expression called, in order")
    check(line[COLUMNS.index("reasons")].startswith("$map the map the "),
          "the reasons cell is a readable sentence per blocking call")
    check(line[COLUMNS.index("reasons")].count(" | ") == 2,
          "with the three reasons separated by something that is not the "
          "semicolon a reason can hold itself  <-- pinned defect")
    check(line[COLUMNS.index("chars")] == 93,
          "the size of the expression is written out, 93 characters here")
    check(isinstance(line[COLUMNS.index("chars")], int),
          "as a number rather than a string, so a spreadsheet can sum it")
    check(csv_row(by_name["Owner mailing"])[COLUMNS.index("reasons")] == "",
          "a portable expression has an empty reasons cell")
    check(all(isinstance(cell, (str, int)) for cell in line),
          "every cell is something csv can write without a converter")

    # ---- the command line, before anything runs
    args = _parse([])
    check(args.apply is False,
          "--apply is off by default, so nothing is written  <-- pinned defect")
    check(args.insecure is False,
          "--insecure is off by default, so tls is verified  <-- pinned defect")
    check(args.self_test is False, "and the self-test is not the default mode")
    check(args.show_all is False, "the report lists only what needs work")
    check(args.files == [], "no files are assumed")
    check(args.item == [], "no items are assumed")
    check(args.portal is None and args.token is None and args.out is None,
          "and no portal, token or output file is assumed")
    check(_parse(["--apply"]).apply is True, "--apply is read")

    def parse_prefix():
        try:
            _parse(["--ap"])
        except SystemExit as exc:
            return exc.code
        return None

    prefix_code, prefix_out = capture(parse_prefix)
    check(prefix_code == 2 and "--ap" in prefix_out,
          "a unique prefix of --apply, --ap, is refused rather than read as "
          "--apply, so a typo cannot write a file  <-- pinned defect")
    check(_parse(["--insecure"]).insecure is True, "--insecure is read")
    check(_parse(["--self-test"]).self_test is True, "--self-test is read")
    check(_parse(["--all"]).show_all is True, "--all is read")
    check(_parse(["a.json", "b.json"]).files == ["a.json", "b.json"],
          "the files are read in the order they were given")
    check(_parse(["--out", "x.csv"]).out == "x.csv", "--out is read")
    check(_parse(["--portal", "https://x"]).portal == "https://x",
          "--portal is read")
    check(_parse(["--token", "T"]).token == "T", "--token is read")
    check(_parse(["--item", "a", "--item", "b"]).item == ["a", "b"],
          "--item can be given more than once and keeps both")
    check(not hasattr(_parse([]), "password"),
          "there is no --password at all, because argv is readable by every "
          "process on the box  <-- pinned defect")
    def parse_nonsense():
        try:
            _parse(["--nonsense"])
        except SystemExit as exc:
            return exc.code
        return None

    code, out = capture(parse_nonsense)
    check(code == 2, "an unknown flag is refused")
    check("nonsense" in out, "and argparse says which flag it was")

    # ---- the command line, end to end
    here = os.getcwd()
    temp = tempfile.mkdtemp(prefix="arcadecheck-")
    try:
        os.chdir(temp)
        for name, body in (("webmap.json", WEBMAP_JSON),
                           ("dashboard.json", DASHBOARD_JSON),
                           ("rules.json", RULES_JSON)):
            with io.open(name, "w", encoding="utf-8") as handle:
                handle.write(body)
        with io.open("portable.json", "w", encoding="utf-8") as handle:
            handle.write(u'{"popupInfo": {"expressionInfos": [{"name": "n", '
                         u'"expression": "$feature.A + 1"}]}}')
        with io.open("broken.json", "w", encoding="utf-8") as handle:
            handle.write(u"{not json")
        with io.open("bom.json", "w", encoding="utf-8-sig") as handle:
            handle.write(u'{"popupInfo": {"expressionInfos": [{"name": "n", '
                         u'"expression": "$feature.A + 1"}]}}')

        code, out = capture(lambda: main([]))
        check(code == 64, "no arguments at all is a usage error")
        check("--self-test" in out,
              "and the message says how to check the tool without one")
        code, out = capture(lambda: main(["--item", "abc"]))
        check(code == 64, "an item with no portal is a usage error")
        code, out = capture(lambda: main(["webmap.json", "--portal",
                                          "https://x"]))
        check(code == 64 and "at least one --item" in out,
              "a portal with no item is a usage error, even beside a file "
              "that would have read cleanly")
        code, out = capture(lambda: main(["--portal", "county.maps.arcgis.com",
                                          "--item", "abc"]))
        check(code == 64,
              "a portal url with no scheme is refused before any request is "
              "made, because urllib would quote the token back  "
              "<-- pinned defect")
        code, out = capture(lambda: main(["--portal", "https://x",
                                          "--item", "a b"]))
        check(code == 64, "an item id that is not an item id is refused too")
        code, out = capture(lambda: main(["nosuchfile.json"]))
        check(code == 2 and "could not read" in out,
              "a file that is not there is a read failure")
        code, out = capture(lambda: main(["broken.json"]))
        check(code == 2 and "not JSON" in out,
              "a file that is not json is a read failure")
        with io.open("bom.json", "rb") as handle:
            check(handle.read(3) == b"\xef\xbb\xbf",
                  "an item document saved out of a browser starts with a byte "
                  "order mark")
        code, out = capture(lambda: main(["bom.json"]))
        check(code == 0 and "1 expression(s) in 1 document(s)" in out,
              "and that mark is read as a mark rather than as the first "
              "character of broken json  <-- pinned defect")

        code, out = capture(lambda: main(["webmap.json"]))
        check(code == 1,
              "a web map holding an esri only expression exits 1")
        check("ESRI ONLY" in out and "Neighbours" in out,
              "and names the expression that cannot move")
        code, out = capture(lambda: main(["portable.json"]))
        check(code == 0, "a document whose arcade all moves exits 0")
        check("1 expression(s) in 1 document(s)" in out,
              "and reports the one portable expression it found")
        code, out = capture(lambda: main(["webmap.json", "dashboard.json",
                                          "rules.json"]))
        check(code == 1, "three documents at once still exit on the worst one")
        check("18 expression(s) in 3 document(s)" in out,
              "and the totals are added up across all three")

        code, out = capture(lambda: main(["webmap.json", "--out", "x.csv"]))
        check(code == 1 and "Would write 10 row(s)" in out,
              "--out without --apply says what it would write")
        check(not os.path.exists("x.csv"),
              "and writes nothing  <-- pinned defect")
        code, out = capture(lambda: main(["webmap.json", "--out", "x.csv",
                                          "--apply"]))
        check(code == 1 and os.path.exists("x.csv"),
              "--apply writes the csv")
        with io.open("x.csv", "r", encoding=CSV_ENCODING, newline="") as handle:
            written = handle.read()
        check(written.startswith("source,document,where"),
              "the csv starts with its header row")
        check("\r\r\n" not in written and "\n\n" not in written,
              "and has no blank line between rows, which is what newline='' "
              "prevents on windows  <-- pinned defect")
        with io.open("x.csv", "r", encoding=CSV_ENCODING, newline="") as handle:
            read_back = list(csv.reader(handle))
        check(read_back[0] == ["source", "document", "where", "layer",
                               "name", "rating", "reasons", "calls", "chars",
                               "lines", "path", "expression"],
              "the header names every column in the order a reader expects, "
              "written out here rather than read back from COLUMNS  "
              "<-- pinned defect")
        check(len(read_back) == 11,
              "and ten rows follow it")
        accents = [row for row in read_back if "R\u00e9sum\u00e9" in row[4]]
        check(len(accents) == 1,
              "a non-ascii expression survives the round trip through the csv")
        multiline = [row for row in read_back if "\n" in row[-1]]
        check(len(multiline) == 2,
              "and an expression written over several lines is quoted rather "
              "than split into several rows")
        with io.open("x.csv", "rb") as handle:
            head = handle.read(3)
        check(head == b"\xef\xbb\xbf",
              "the csv carries a byte order mark, or excel opens the accented "
              "rows in the wrong codepage")
        code, out = capture(lambda: main(["webmap.json", "--out", "x.csv",
                                          "--apply"]))
        check(code == 2 and "already exists" in out,
              "a second run refuses to overwrite the csv  <-- pinned defect")
        code, out = capture(lambda: main(["webmap.json", "--all"]))
        check("Owner mailing" in out,
              "--all lists the portable expressions as well")
        code, out = capture(lambda: main(["webmap.json", "--out",
                                          os.path.join("nodir", "x.csv"),
                                          "--apply"]))
        check(code == 2 and "could not write" in out,
              "a csv that cannot be written is a write failure, not a "
              "traceback")

        # ---- a portal, over a real socket on the loopback address
        secret = "AAPK-self-test-token-not-a-real-one"
        served = {"webmap": WEBMAP_JSON}

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def log_message(self, *args):
                pass

            def send(self, code, body, kind="application/json"):
                raw = body.encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                parts = urlsplit(self.path)
                token = (parse_qs(parts.query).get("token") or [""])[0]
                pieces = parts.path.strip("/").split("/")
                item = pieces[-2] if len(pieces) > 1 else ""
                if item == "goodmap":
                    if token != secret:
                        self.send(200, json.dumps(
                            {"error": {"code": 499, "message":
                                       "Token Required"}}))
                    else:
                        self.send(200, served["webmap"])
                elif item == "echoitem":
                    # A portal that quotes the token back in its own error
                    # message. Esri's does not, but an Enterprise proxy in
                    # front of one has, and the tool must not print it.
                    self.send(200, json.dumps(
                        {"error": {"code": 498,
                                   "message": "Invalid token " + token}}))
                elif item == "echoquery":
                    # Hands the raw query string back as the item document, so
                    # the self-test can read what went on the wire instead of
                    # trusting that it was built right.
                    self.send(200, json.dumps({"query": parts.query}))
                elif item == "notjson":
                    self.send(200, "<html>not json</html>", "text/html")
                elif item == "arraydoc":
                    self.send(200, "[1, 2, 3]")
                else:
                    self.send(404, json.dumps({"error": "no such item"}))

        server = HTTPServer(("127.0.0.1", 0), Handler)
        base = "http://127.0.0.1:%d" % server.server_address[1]
        thread = threading.Thread(target=server.serve_forever)
        thread.daemon = True
        thread.start()
        try:
            check(item_url("https://x", "abc")
                  == "https://x/sharing/rest/content/items/abc/data",
                  "the item data url is built from the portal and the id")
            check(item_url("https://x/", "abc") == item_url("https://x", "abc"),
                  "a trailing slash on the portal makes no difference")
            raises(lambda: item_url("county.maps.arcgis.com", "abc"),
                   "a portal url with no scheme is refused")
            raises(lambda: item_url("ftp://x", "abc"),
                   "and so is a scheme that is not http")
            raises(lambda: item_url("https://x", "../../etc/passwd"),
                   "an item id that is a path is refused")
            raises(lambda: item_url("https://x", ""),
                   "and so is an empty one")
            check(ssl_context(False) is None,
                  "tls verification is left alone by default")
            check(ssl_context(True).verify_mode == ssl.CERT_NONE,
                  "--insecure turns certificate checking off")
            check(ssl_context(True).check_hostname is False,
                  "and hostname checking with it")

            check(fetch_item(base, "echoquery")["query"] == "f=json",
                  "a read with no token puts no token on the wire at all, "
                  "not an empty one  <-- pinned defect")
            awkward = "a/b+c d&e"
            echoed = fetch_item(base, "echoquery", awkward)["query"]
            check(echoed == "f=json&token=a%2Fb%2Bc%20d%26e",
                  "and a token is percent encoded whole, so a slash or an "
                  "ampersand inside one cannot split the query  "
                  "<-- pinned defect")
            check(parse_qs(echoed)["token"] == [awkward],
                  "which is the same token again once the portal parses it")
            check(parse_qs(echoed)["f"] == ["json"],
                  "and f=json is asked for, or a portal answers html")

            fetched = fetch_item(base, "goodmap", secret)
            check(detect(fetched) == "webmap",
                  "an item read from the portal comes back as its document")
            check(len(collect(fetched, "goodmap")) == 10,
                  "and it holds the ten expressions the file held")
            message = raises(lambda: fetch_item(base, "goodmap"),
                             "an item read with no token is refused by the "
                             "portal")
            check("Token Required" in message,
                  "and the portal's own words are reported")
            message = raises(lambda: fetch_item(base, "echoitem", secret),
                             "a portal error is raised rather than returned")
            check(secret not in message,
                  "and a token the portal echoed back is not in the message  "
                  "<-- pinned defect")
            check("[redacted]" in message,
                  "it is replaced, so the message still reads")
            message = raises(lambda: fetch_item(base, "notjson", secret),
                             "a portal that answers html is a failure")
            check("did not answer with JSON" in message,
                  "and says so rather than raising a decoder error")
            message = raises(lambda: fetch_item(base, "arraydoc", secret),
                             "a portal that answers a json array is a failure")
            check("not an item document" in message,
                  "because an item document is an object")
            message = raises(lambda: fetch_item(base, "missing", secret),
                             "an item that is not there is a failure")
            check("404" in message, "and the status code is reported")

            code, out = capture(lambda: main(["--portal", base, "--item",
                                              "goodmap", "--token", secret]))
            check(code == 1,
                  "the command line reads an item out of a portal and rates it")
            check("10 expression(s) in 1 document(s)" in out,
                  "and reports the same ten expressions")
            check(secret not in out,
                  "the token never reaches stdout  <-- pinned defect")
            check("goodmap" in out,
                  "the item id is used as the source name")

            os.environ["ARCADECHECK_TOKEN"] = secret
            try:
                code, out = capture(lambda: main(["--portal", base, "--item",
                                                  "goodmap", "--out", "p.csv",
                                                  "--apply"]))
            finally:
                del os.environ["ARCADECHECK_TOKEN"]
            check(code == 1,
                  "the token can come from the environment instead of argv")
            os.environ["ARCADECHECK_TOKEN"] = "the-wrong-token-entirely"
            try:
                code, out = capture(lambda: main(["--portal", base, "--item",
                                                  "goodmap", "--token",
                                                  secret]))
            finally:
                del os.environ["ARCADECHECK_TOKEN"]
            check(code == 1 and "10 expression(s)" in out,
                  "a --token on the command line beats a stale one left in "
                  "the environment  <-- pinned defect")
            check(secret not in out and "the-wrong-token-entirely" not in out,
                  "and neither of the two reaches stdout")
            with io.open("p.csv", "r", encoding=CSV_ENCODING) as handle:
                on_disk = handle.read()
            check(secret not in on_disk,
                  "and no credential reaches the csv on disk  <-- pinned defect")
            with io.open("p.csv", "r", encoding=CSV_ENCODING,
                         newline="") as handle:
                check(len(list(csv.reader(handle))) == 11,
                      "which holds the header row and the ten expressions")
            code, out = capture(lambda: main(["--portal", base,
                                              "--item", "missing"]))
            check(code == 2 and "404" in out,
                  "an item the portal will not give up exits 2")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        message = raises(lambda: fetch_item(base, "goodmap", secret),
                         "a portal that is not listening is a failure")
        check("could not reach the portal" in message,
              "and is reported as unreachable rather than as a bad item")
        check(secret not in message,
              "with no token in that message either")
    finally:
        os.chdir(here)
        shutil.rmtree(temp, ignore_errors=True)
    check(not os.path.isdir(temp),
          "the self-test leaves no temporary directory behind  "
          "<-- pinned defect")

    # ---- the console the report has to survive
    stream = _AsciiOnlyStream()
    emit("R\u00e9sum\u00e9", stream)
    check("R" in stream.text and "\u00e9" not in stream.text,
          "a console that cannot carry an accent gets it escaped rather than "
          "a traceback  <-- pinned defect")
    check("\\xe9" in stream.text,
          "and the escape says which character it was")

    # ---- the harness itself
    quiet_check, quiet_raises, quiet_passed, quiet_failed = _harness(quiet=True)
    quiet_check(True, "one that holds")
    quiet_check(False, "one that does not")
    quiet_raises(lambda: None, "one that should have raised")
    quiet_raises(lambda: 1 / 0, "one that raised the wrong thing")
    check(quiet_passed[0] == 1 and len(quiet_failed) == 3,
          "the harness records a false check, a missing exception and a wrong "
          "exception as three failures, so a broken tool turns this self-test "
          "red  <-- pinned defect")

    def failing_harness():
        loud_check = _harness()[0]
        loud_check(False, "this one is meant to fail")

    _, loud = capture(failing_harness)
    check(loud.startswith("FAIL  this one is meant to fail"),
          "and a failure is printed as FAIL, so the transcript shows which "
          "assertion went red")

    print("-" * 68)
    total = passed[0] + len(failed)
    if failed:
        print("%d assertions, %d failed" % (total, len(failed)))
        for label in failed:
            print("  FAILED: %s" % label)
        return 1
    print("%d assertions, 0 failed" % total)
    return 0


# ----------------------------------------------------------------------- cli

def emit(text, stream=None):
    """Print one block of text, whatever the stream can encode.

    Windows hands a REDIRECTED stdout the ANSI codepage rather than UTF-8, so an
    accented string inside an Arcade expression makes print() raise
    UnicodeEncodeError and the tool dies with a traceback instead of an exit
    code. Linux defaults to UTF-8 and never shows this, which is why it has to
    be handled here rather than noticed later.

    The characters the stream cannot carry are escaped rather than dropped, so
    the expression stays identifiable and the report still exits cleanly.
    """
    stream = sys.stdout if stream is None else stream
    try:
        print(text, file=stream)
    except UnicodeEncodeError:
        encoding = getattr(stream, "encoding", None) or "ascii"
        print(text.encode(encoding, "backslashreplace").decode(encoding),
              file=stream)


def read_document(path):
    """The JSON document in one file.

    utf-8-sig, not utf-8: an item document downloaded through a browser or
    edited in Notepad carries a byte order mark, and utf-8 reads that mark as a
    character, which makes json.loads refuse a file that is perfectly good.
    """
    try:
        with io.open(path, "r", encoding="utf-8-sig") as handle:
            text = handle.read()
    except (OSError, IOError) as exc:
        raise ValueError("could not read %s: %s" % (path, exc))
    try:
        return json.loads(text)
    except ValueError as exc:
        raise ValueError("%s is not JSON: %s" % (path, exc))


def ssl_context(insecure):
    """An unverified context for --insecure, or None to leave the default alone.

    None rather than a verifying context on purpose: urlopen already verifies,
    and building a context here would replace the interpreter's certificate
    store with whatever this one happened to load.
    """
    if not insecure:
        return None
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def item_url(portal, item_id):
    """The data url of one item, or a refusal.

    Both halves are checked before a token is ever appended. urllib puts the
    whole url into the exception it raises for a scheme it does not know, so a
    typo in the portal name would otherwise print the token in a traceback.
    """
    if not isinstance(portal, str) or not portal.lower().startswith(
            ("https://", "http://")):
        raise ValueError("portal url must start with https:// or http://")
    if not isinstance(item_id, str) or not re.match(r"^[A-Za-z0-9]+$", item_id):
        raise ValueError("item id must be letters and digits")
    return portal.rstrip("/") + ITEM_DATA_PATH % item_id


def fetch_item(portal, item_id, token=None, insecure=False):
    """One item's data document, read from a portal. Nothing is written.

    /data is a GET endpoint, so the token travels in the query string and the
    url itself is a secret. It is built here, used once and never returned, and
    nothing raised out of this function carries it: a portal that quotes the
    token back inside its own error message gets that copy masked as well.
    """
    url = item_url(portal, item_id)
    query = "f=json"
    if token:
        query += "&token=" + quote(token, safe="")
    try:
        handle = urlopen(url + "?" + query, timeout=TIMEOUT,
                         context=ssl_context(insecure))
    except HTTPError as exc:
        raise ValueError("portal answered %s for item %s" % (exc.code, item_id))
    except URLError as exc:
        raise ValueError("could not reach the portal for item %s: %s"
                         % (item_id, exc.reason))
    try:
        raw = handle.read()
    finally:
        handle.close()
    try:
        doc = json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        raise ValueError("item %s did not answer with JSON" % item_id)
    if not isinstance(doc, dict):
        raise ValueError("item %s answered with a JSON %s, which is not an "
                         "item document" % (item_id, type(doc).__name__))
    if isinstance(doc.get("error"), dict):
        detail = doc["error"].get("message") or "no message"
        if token and token in detail:
            detail = detail.replace(token, "[redacted]")
        raise ValueError("portal refused item %s: %s" % (item_id, detail))
    return doc


def write_csv(path, records):
    """Write the inventory. newline='' is what stops a blank line between rows."""
    with io.open(path, "w", encoding=CSV_ENCODING, newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(list(COLUMNS))
        for row in records:
            writer.writerow(csv_row(row))


def _parse(argv):
    parser = argparse.ArgumentParser(
        prog="arcadecheck.py",
        description="Inventory every Arcade expression you own and say which "
                    "ones can leave with you.",
        epilog="Attribute rules are read from gdbxray --json output, not from "
               "the geodatabase. Nothing is written without --apply.",
        allow_abbrev=False,
    )
    parser.add_argument("files", nargs="*", metavar="FILE",
                        help="web map, dashboard, form or gdbxray --json "
                             "documents to read")
    parser.add_argument("--portal", metavar="URL",
                        help="portal to read items from. Must start with "
                             "https:// or http://.")
    parser.add_argument("--item", metavar="ID", action="append", default=[],
                        help="item id to read from --portal. Repeatable.")
    parser.add_argument("--token", metavar="TOKEN",
                        help="portal token. Env: ARCADECHECK_TOKEN, which "
                             "keeps it out of the process list.")
    parser.add_argument("--insecure", action="store_true",
                        help="skip TLS verification, for an Enterprise portal "
                             "behind an internal CA. OFF by default.")
    parser.add_argument("--all", dest="show_all", action="store_true",
                        help="list the portable expressions too, not only the "
                             "ones that need work")
    parser.add_argument("--out", metavar="FILE",
                        help="CSV file to write the inventory to. Needs "
                             "--apply to write.")
    parser.add_argument("--apply", action="store_true",
                        help="write the --out file. Without this nothing is "
                             "written, and an existing file is never "
                             "overwritten.")
    parser.add_argument("--self-test", dest="self_test", action="store_true",
                        help="run the offline assertions and exit. Needs no "
                             "portal and no geodatabase.")
    return parser.parse_args(argv)


def main(argv=None):
    args = _parse(sys.argv[1:] if argv is None else argv)

    if args.self_test:
        return self_test()

    if not args.files and not args.item:
        emit("error: name at least one JSON file, or a --portal and an "
             "--item. Use --self-test to check the tool without either.",
             sys.stderr)
        return 64
    if args.item and not args.portal:
        emit("error: --item needs a --portal to read it from.", sys.stderr)
        return 64
    if args.portal and not args.item:
        emit("error: --portal needs at least one --item to read.", sys.stderr)
        return 64
    for item_id in args.item:
        try:
            item_url(args.portal, item_id)
        except ValueError as exc:
            emit("error: %s" % exc, sys.stderr)
            return 64

    # The environment is second, not first: a flag on the command line is the
    # more deliberate of the two.
    token = args.token or os.environ.get("ARCADECHECK_TOKEN") or None

    documents = []
    for path in args.files:
        try:
            documents.append((read_document(path), path))
        except ValueError as exc:
            emit("error: %s" % exc, sys.stderr)
            return 2
    for item_id in args.item:
        try:
            documents.append((fetch_item(args.portal, item_id, token,
                                         args.insecure), item_id))
        except ValueError as exc:
            emit("error: %s" % exc, sys.stderr)
            return 2

    records = []
    for doc, source in documents:
        records.extend(collect(doc, source))
    summary = summarise(records)
    emit("\n".join(render(records, summary, args.show_all)))

    if args.out:
        if not args.apply:
            emit("Would write %d row(s) to %s. Re-run with --apply to write "
                 "it." % (len(records), args.out))
        elif os.path.exists(args.out):
            emit("error: %s already exists. Refusing to overwrite it."
                 % args.out, sys.stderr)
            return 2
        else:
            try:
                write_csv(args.out, records)
            except (OSError, IOError) as exc:
                emit("error: could not write %s: %s" % (args.out, exc),
                     sys.stderr)
                return 2
            emit("Wrote %s" % args.out)

    return 0 if summary["worst"] == PORTABLE else 1


if __name__ == "__main__":
    sys.exit(main())
