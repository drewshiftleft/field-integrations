# Usage: python3 export.py -a app name

import argparse
import csv
import json
import os
import sys
import time

import joern2sarif.lib.convert as convertLib
from json2xml import json2xml
from rich.progress import Progress

import config
from common import (
    extract_org_id,
    get_all_apps,
    get_all_findings,
    get_dataflow,
    get_findings_url,
)


def export_csv(app_list, findings_dict, report_file):
    with open(report_file, "w", newline="") as csvfile:
        reportwriter = csv.writer(
            csvfile, delimiter=",", quotechar='"', quoting=csv.QUOTE_MINIMAL
        )
        reportwriter.writerow(
            [
                "App",
                "App Group",
                "Finding ID",
                "Category",
                "OWASP Category",
                "Severity",
                "Source Method",
                "Sink Method",
                "Source File",
            ]
        )
        for app in app_list:
            app_id = app.get("id")
            app_name = app.get("name")
            tags = app.get("tags")
            app_group = ""
            if tags:
                for tag in tags:
                    if tag.get("key") == "group":
                        app_group = tag.get("value")
                        break
            findings = findings_dict[app_name]
            source_method = ""
            sink_method = ""
            files_loc_list = set()
            # Find the source and sink
            for afinding in findings:
                details = afinding.get("details", {})
                source_method = details.get("source_method", "")
                sink_method = details.get("sink_method", "")
                if details.get("file_locations"):
                    files_loc_list.update(details.get("file_locations"))
                # For old scans, details block might be empty.
                # We go old school and iterate all dataflows
                if not source_method or not sink_method or not files_loc_list:
                    dfobj = {}
                    if details.get("dataflow"):
                        dfobj = details.get("dataflow")
                    dataflows = dfobj.get("list", [])
                    files_loc_list = set()
                    for df in dataflows:
                        location = df.get("location", {})
                        if location.get("file_name") == "N/A" or not location.get(
                            "line_number"
                        ):
                            continue
                        if not source_method:
                            source_method = f'{location.get("file_name")}:{location.get("line_number")}'
                        files_loc_list.add(
                            f'{location.get("file_name")}:{location.get("line_number")}'
                        )
                    if dataflows and dataflows[-1]:
                        sink = dataflows[-1].get("location", {})
                        if sink:
                            sink_method = (
                                f'{sink.get("file_name")}:{sink.get("line_number")}'
                            )
                for loc in files_loc_list:
                    reportwriter.writerow(
                        [
                            app_name,
                            app_group,
                            afinding.get("id"),
                            afinding.get("category"),
                            afinding.get("owasp_category"),
                            afinding.get("severity"),
                            source_method,
                            sink_method,
                            loc,
                        ]
                    )


def export_report(org_id, app_list, report_file, format):
    if not app_list:
        app_list = get_all_apps(org_id)
    findings_dict = {}
    work_dir = os.getcwd()
    for e in ["GITHUB_WORKSPACE", "WORKSPACE"]:
        if os.getenv(e):
            work_dir = os.getenv(e)
            break
    with Progress(
        transient=True,
        redirect_stderr=False,
        redirect_stdout=False,
        refresh_per_second=1,
    ) as progress:
        task = progress.add_task(
            "[green] Export Findings", total=len(app_list), start=True
        )
        for app in app_list:
            app_id = app.get("id")
            app_name = app.get("name")
            progress.update(task, description=f"Processing [bold]{app_name}[/bold]")
            findings = get_all_findings(org_id, app_id, None)
            file_category_set = set()
            if format == "xml" or report_file.endswith(".xml"):
                app_report_file = report_file.replace(".xml", "-" + app_id + ".xml")
                with open(app_report_file, mode="w") as rp:
                    xml_data = json2xml.Json2xml(findings).to_xml()
                    if xml_data:
                        rp.write(xml_data)
                        progress.console.print(
                            f"Findings report successfully exported to {app_report_file}"
                        )
            elif format == "sarif":
                app_sarif_file = report_file.replace(".sarif", "-" + app_id + ".sarif")
                app_json_file = app_sarif_file.replace(".sarif", ".json")
                with open(app_json_file, mode="w") as rp:
                    json.dump(
                        {app_name: findings},
                        rp,
                        ensure_ascii=True,
                        indent=None,
                    )
                    rp.flush()
                convertLib.convert_file(
                    "ng-sast",
                    os.getenv("TOOL_ARGS", ""),
                    work_dir,
                    app_json_file,
                    app_sarif_file,
                    None,
                )
                progress.console.print(
                    f"SARIF file successfully exported to {app_sarif_file}"
                )
                os.remove(app_json_file)
            elif format == "sl":
                with open(report_file, mode="w") as rp:
                    for af in findings:
                        details = af.get("details")
                        title = af.get("title")
                        # filename could be found either in file_locations or fileName
                        filename = ""
                        if details and details.get("file_locations"):
                            file_locations = details.get("file_locations")
                            if len(file_locations):
                                filename = (
                                    file_locations[0].split(":")[0].split("/")[-1]
                                )
                                filename = filename.replace(".java", "")
                        # If there is no file_locations try to extract the name from the title
                        if not filename and "BenchmarkTest" in title:
                            filename = (
                                title.split(" in ")[-1].replace("`", "").split(".")[0]
                            )
                        if filename.startswith("BenchmarkTest"):
                            filename = filename.replace("BenchmarkTest", "")
                        else:
                            # Try to get the filename from source_method in details
                            source_method = details.get("source_method")
                            if source_method and "BenchmarkTest" in source_method:
                                filename = source_method.split(":")[0].split(".")[4]
                                filename = filename.replace("BenchmarkTest", "")
                            else:
                                progress.console.print(
                                    f'Get dataflow for {af.get("id")}'
                                )
                                # dataflows = get_dataflow(app_name, af.get("id"))
                                dataflows = details.get("dataflow", {}).get("list")
                                if dataflows:
                                    for df in dataflows:
                                        location = df.get("location")
                                        if location.get(
                                            "class_name"
                                        ) and "BenchmarkTest" in location.get(
                                            "class_name"
                                        ):
                                            filename = location.get("class_name").split(
                                                "."
                                            )[-1]
                                            filename = filename.replace(
                                                "BenchmarkTest", ""
                                            )
                                            break
                        if not filename:
                            progress.console.print(
                                f"Unable to extract filename from file_locations or title {title}. Skipping ..."
                            )
                            continue
                        tags = af.get("tags")
                        cwe_id = ""
                        category = af.get("title")
                        for tag in tags:
                            if tag["key"] == "category":
                                category = tag["value"]
                            if tag["key"] == "cwe_category":
                                cwe_id = tag["value"]
                        if not category and cwe_id == "384":
                            category = "Broken Authentication"
                        if config.sl_owasp_category.get(category):
                            category = config.sl_owasp_category.get(category)
                        file_category = f"{filename},{category}"
                        if (
                            " " not in category
                            and file_category not in file_category_set
                        ):
                            rp.write(file_category + "\n")
                            file_category_set.add(file_category)
                    progress.console.print(
                        f"Findings report successfully exported to {report_file}"
                    )
            else:
                findings_dict[app_name] = findings
            progress.advance(task)
    if format == "json":
        with open(report_file, mode="w") as rp:
            json.dump(findings_dict, rp, ensure_ascii=True, indent=config.json_indent)
            print(f"JSON report successfully exported to {report_file}")
    if format == "csv":
        export_csv(app_list, findings_dict, report_file)
        print(f"CSV report successfully exported to {report_file}")


def build_args():
    """
    Constructs command line arguments for the export script
    """
    parser = argparse.ArgumentParser(description="ShiftLeft NG SAST export script")
    parser.add_argument(
        "-a",
        "--app",
        dest="app_name",
        help="App name",
        default=config.SHIFTLEFT_APP,
    )
    parser.add_argument(
        "-o",
        "--report_file",
        dest="report_file",
        help="Report filename",
        default="ngsast-report.json",
    )
    parser.add_argument(
        "-f",
        "--format",
        dest="format",
        help="Report format",
        default="json",
        choices=["json", "xml", "csv", "sl", "sarif"],
    )
    return parser.parse_args()


if __name__ == "__main__":
    if not config.SHIFTLEFT_ACCESS_TOKEN:
        print(
            "Set the environment variable SHIFTLEFT_ACCESS_TOKEN before running this script"
        )
        sys.exit(1)

    org_id = extract_org_id(config.SHIFTLEFT_ACCESS_TOKEN)
    if not org_id:
        print(
            "Ensure the environment varibale SHIFTLEFT_ACCESS_TOKEN is copied exactly as-is from the website"
        )
        sys.exit(1)

    print(config.ngsast_logo)
    start_time = time.monotonic_ns()
    args = build_args()
    app_list = []
    if args.app_name:
        app_list.append({"id": args.app_name, "name": args.app_name})
    report_file = args.report_file
    format = args.format
    # Fix file extensions for xml format
    if format == "xml":
        report_file = report_file.replace(".json", ".xml")
    if format == "sarif":
        report_file = report_file.replace(".json", ".sarif")
    if format == "csv":
        report_file = report_file.replace(".json", ".csv")
    elif format == "sl":
        if not args.app_name:
            print(
                "This format is only suitable for OWASP Benchmark purposes. Use json or csv for all other apps"
            )
            sys.exit(1)
        if not report_file:
            report_file = "Benchmark_1.2-ShiftLeft.sl"
    export_report(org_id, app_list, report_file, format)
    end_time = time.monotonic_ns()
    total_time_sec = round((end_time - start_time) / 1000000000, 2)
