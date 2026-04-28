#!/usr/bin/env python3
"""NCC Comprehensive Monitor

Writes custom metrics to Cloud Monitoring on every run, then creates or updates
a dashboard covering:
  • NCC spoke health (status and counts)
  • NCC route inventory (control-plane route table counts)
  • Cloud Router BGP peer health (session up/down, learned route counts)
  • VPN tunnel health (ESTABLISHED or not)
  • Interconnect VLAN attachment health (OS_ACTIVE or not)
  • Cloud Router prefix quotas (propagation proxy)

Custom metrics written each run:
  custom.googleapis.com/ncc/spoke_status        – 1=ACTIVE/0=other, per spoke
  custom.googleapis.com/ncc/spoke_count         – spoke count by hub and state
  custom.googleapis.com/ncc/route_count         – NCC route count by hub/route-table
  custom.googleapis.com/ncc/bgp_peer_up         – 1=UP/0=DOWN, per BGP peer
  custom.googleapis.com/ncc/bgp_learned_routes  – routes learned per BGP peer
  custom.googleapis.com/ncc/vpn_tunnel_up       – 1=ESTABLISHED/0=other, per tunnel
  custom.googleapis.com/ncc/interconnect_up     – 1=OS_ACTIVE/0=other, per attachment

Platform metrics referenced in the dashboard (written by GCP automatically):
  compute.googleapis.com/quota/cloud_router_prefixes_from_other_regions.../usage|limit
  compute.googleapis.com/quota/cloud_router_prefixes_from_own_region.../usage|limit

Note: compute.googleapis.com/router/bgp/* per-session metrics are only emitted
for Dedicated/Partner Interconnect — not for HA VPN or NCC VPN spokes.

Install dependencies:
    pip install google-cloud-network-connectivity \\
                google-cloud-monitoring \\
                google-cloud-monitoring-dashboards \\
                google-cloud-compute
"""

import collections
import os
import sys
import time
from typing import Any

from google.api_core.exceptions import AlreadyExists
from google.cloud import compute_v1
from google.cloud import monitoring_v3
from google.cloud import networkconnectivity_v1 as network_connectivity_v1
from google.cloud import monitoring_dashboard_v1 as dashboard_v1
from google.protobuf import duration_pb2

# ── Configuration ──────────────────────────────────────────────────────────────
PROJECT_ID = os.environ.get("PROJECT_ID", "")
if not PROJECT_ID:
    sys.exit("ERROR: PROJECT_ID environment variable is not set.")
PROJECT_NAME = f"projects/{PROJECT_ID}"
DASHBOARD_DISPLAY_NAME = "NCC Comprehensive Monitor"

# Custom metrics (written by this script)
METRIC_SPOKE_STATUS    = "custom.googleapis.com/ncc/spoke_status"
METRIC_SPOKE_COUNT     = "custom.googleapis.com/ncc/spoke_count"
METRIC_ROUTE_COUNT     = "custom.googleapis.com/ncc/route_count"
METRIC_BGP_PEER_UP     = "custom.googleapis.com/ncc/bgp_peer_up"
METRIC_BGP_ROUTES      = "custom.googleapis.com/ncc/bgp_learned_routes"
METRIC_VPN_TUNNEL_UP   = "custom.googleapis.com/ncc/vpn_tunnel_up"
METRIC_INTERCONNECT_UP = "custom.googleapis.com/ncc/interconnect_up"

# Cloud Router quota metrics (platform, referenced in dashboard only)
ROUTER_OTHER_USAGE = "compute.googleapis.com/quota/cloud_router_prefixes_from_other_regions_per_region_per_vpc_network/usage"
ROUTER_OTHER_LIMIT = "compute.googleapis.com/quota/cloud_router_prefixes_from_other_regions_per_region_per_vpc_network/limit"
ROUTER_OWN_USAGE   = "compute.googleapis.com/quota/cloud_router_prefixes_from_own_region_per_region_per_vpc_network/usage"
ROUTER_OWN_LIMIT   = "compute.googleapis.com/quota/cloud_router_prefixes_from_own_region_per_region_per_vpc_network/limit"


# ── Metric descriptors ─────────────────────────────────────────────────────────

def _ensure_descriptor(client: monitoring_v3.MetricServiceClient, descriptor: dict) -> None:
    try:
        client.create_metric_descriptor(name=PROJECT_NAME, metric_descriptor=descriptor)
        print(f"  [created] {descriptor['type']}")
    except AlreadyExists:
        print(f"  [exists]  {descriptor['type']}")


def create_metric_descriptors(client: monitoring_v3.MetricServiceClient) -> None:
    print("[metrics] Ensuring custom metric descriptors...")
    _ensure_descriptor(client, {
        "type": METRIC_SPOKE_STATUS,
        "metric_kind": "GAUGE", "value_type": "INT64", "unit": "1",
        "description": "NCC spoke health: 1=ACTIVE, 0=any other state.",
        "display_name": "NCC Spoke Status",
        "labels": [
            {"key": "spoke_name", "value_type": "STRING", "description": "Spoke name"},
            {"key": "hub",        "value_type": "STRING", "description": "Parent hub name"},
            {"key": "location",   "value_type": "STRING", "description": "GCP region / global"},
            {"key": "state",      "value_type": "STRING", "description": "Raw spoke state"},
        ],
    })
    _ensure_descriptor(client, {
        "type": METRIC_SPOKE_COUNT,
        "metric_kind": "GAUGE", "value_type": "INT64", "unit": "1",
        "description": "Number of NCC spokes grouped by hub and state.",
        "display_name": "NCC Spoke Count",
        "labels": [
            {"key": "hub",   "value_type": "STRING", "description": "Parent hub name"},
            {"key": "state", "value_type": "STRING", "description": "Spoke state"},
        ],
    })
    _ensure_descriptor(client, {
        "type": METRIC_ROUTE_COUNT,
        "metric_kind": "GAUGE", "value_type": "INT64", "unit": "1",
        "description": "Number of routes in an NCC hub route table.",
        "display_name": "NCC Route Count",
        "labels": [
            {"key": "hub",         "value_type": "STRING", "description": "Hub name"},
            {"key": "route_table", "value_type": "STRING", "description": "Route table name"},
            {"key": "route_state", "value_type": "STRING", "description": "Route state"},
        ],
    })
    _ensure_descriptor(client, {
        "type": METRIC_BGP_PEER_UP,
        "metric_kind": "GAUGE", "value_type": "INT64", "unit": "1",
        "description": "Cloud Router BGP peer health: 1=UP, 0=DOWN.",
        "display_name": "BGP Peer Up",
        "labels": [
            {"key": "router",    "value_type": "STRING", "description": "Cloud Router name"},
            {"key": "region",    "value_type": "STRING", "description": "GCP region"},
            {"key": "network",   "value_type": "STRING", "description": "VPC network name"},
            {"key": "peer_name", "value_type": "STRING", "description": "BGP peer name"},
        ],
    })
    _ensure_descriptor(client, {
        "type": METRIC_BGP_ROUTES,
        "metric_kind": "GAUGE", "value_type": "INT64", "unit": "1",
        "description": "Number of routes learned from a Cloud Router BGP peer.",
        "display_name": "BGP Learned Routes",
        "labels": [
            {"key": "router",    "value_type": "STRING", "description": "Cloud Router name"},
            {"key": "region",    "value_type": "STRING", "description": "GCP region"},
            {"key": "network",   "value_type": "STRING", "description": "VPC network name"},
            {"key": "peer_name", "value_type": "STRING", "description": "BGP peer name"},
        ],
    })
    _ensure_descriptor(client, {
        "type": METRIC_VPN_TUNNEL_UP,
        "metric_kind": "GAUGE", "value_type": "INT64", "unit": "1",
        "description": "VPN tunnel health: 1=ESTABLISHED, 0=any other state.",
        "display_name": "VPN Tunnel Up",
        "labels": [
            {"key": "tunnel_name", "value_type": "STRING", "description": "VPN tunnel name"},
            {"key": "region",      "value_type": "STRING", "description": "GCP region"},
            {"key": "gateway",     "value_type": "STRING", "description": "VPN gateway name"},
            {"key": "status",      "value_type": "STRING", "description": "Raw tunnel status"},
        ],
    })
    _ensure_descriptor(client, {
        "type": METRIC_INTERCONNECT_UP,
        "metric_kind": "GAUGE", "value_type": "INT64", "unit": "1",
        "description": "Interconnect VLAN attachment health: 1=OS_ACTIVE, 0=other.",
        "display_name": "Interconnect Attachment Up",
        "labels": [
            {"key": "attachment_name", "value_type": "STRING", "description": "Attachment name"},
            {"key": "region",          "value_type": "STRING", "description": "GCP region"},
            {"key": "interconnect",    "value_type": "STRING", "description": "Parent Interconnect name"},
            {"key": "bandwidth",       "value_type": "STRING", "description": "Provisioned bandwidth"},
            {"key": "op_status",       "value_type": "STRING", "description": "Operational status"},
        ],
    })


# ── Data collection ────────────────────────────────────────────────────────────

def _spoke_type(spoke) -> str:
    if spoke.linked_vpc_network and spoke.linked_vpc_network.uri:
        return "VPC"
    if spoke.linked_vpn_tunnels and spoke.linked_vpn_tunnels.uris:
        return "VPN"
    if spoke.linked_interconnect_attachments and spoke.linked_interconnect_attachments.uris:
        return "INTERCONNECT"
    if spoke.linked_router_appliance_instances and spoke.linked_router_appliance_instances.instances:
        return "ROUTER_APPLIANCE"
    return "UNKNOWN"


def get_spokes(project_id: str) -> list[dict[str, Any]]:
    ncc    = network_connectivity_v1.HubServiceClient()
    parent = f"projects/{project_id}/locations/-"
    results: list[dict[str, Any]] = []
    for spoke in ncc.list_spokes(parent=parent):
        parts = spoke.name.split("/")
        results.append({
            "spoke_name": parts[-1],
            "full_name":  spoke.name,
            "hub":        spoke.hub.split("/")[-1] if spoke.hub else "none",
            "location":   parts[3] if len(parts) > 3 else "unknown",
            "state":      network_connectivity_v1.State(spoke.state).name,
            "is_active":  spoke.state == network_connectivity_v1.State.ACTIVE,
            "spoke_type": _spoke_type(spoke),
        })
    return results


def get_route_counts(project_id: str) -> list[dict[str, Any]]:
    ncc     = network_connectivity_v1.HubServiceClient()
    results: list[dict[str, Any]] = []
    for hub in ncc.list_hubs(parent=f"projects/{project_id}/locations/global"):
        hub_short = hub.name.split("/")[-1]
        for rt in ncc.list_route_tables(parent=hub.name):
            rt_short = rt.name.split("/")[-1]
            counts: collections.Counter = collections.Counter()
            for route in ncc.list_routes(parent=rt.name):
                counts[network_connectivity_v1.State(route.state).name] += 1
            if counts:
                for state_str, count in counts.items():
                    results.append({"hub": hub_short, "route_table": rt_short,
                                    "route_state": state_str, "count": count})
            else:
                results.append({"hub": hub_short, "route_table": rt_short,
                                "route_state": "NONE", "count": 0})
    return results


def get_bgp_peers(project_id: str) -> list[dict[str, Any]]:
    """Poll Cloud Router getStatus for BGP peer health across all regions."""
    routers_client = compute_v1.RoutersClient()
    results: list[dict[str, Any]] = []
    for scope, scoped_list in routers_client.aggregated_list(project=project_id):
        if not scoped_list.routers:
            continue
        region = scope.replace("regions/", "")
        for router in scoped_list.routers:
            network = router.network.split("/")[-1] if router.network else "unknown"
            try:
                status = routers_client.get_router_status(
                    project=project_id, region=region, router=router.name
                )
                for peer in status.result.bgp_peer_status:
                    results.append({
                        "router":             router.name,
                        "region":             region,
                        "network":            network,
                        "peer_name":          peer.name,
                        "is_up":              peer.status == "UP",
                        "status":             peer.status,
                        "num_learned_routes": peer.num_learned_routes,
                    })
            except Exception as exc:
                print(f"  [warn] router {router.name}: {exc}")
    return results


def get_vpn_tunnels(project_id: str) -> list[dict[str, Any]]:
    """Return VPN tunnel status from all regions."""
    client  = compute_v1.VpnTunnelsClient()
    results: list[dict[str, Any]] = []
    for scope, scoped_list in client.aggregated_list(project=project_id):
        if not scoped_list.vpn_tunnels:
            continue
        region = scope.replace("regions/", "")
        for tunnel in scoped_list.vpn_tunnels:
            if tunnel.vpn_gateway:
                gateway = tunnel.vpn_gateway.split("/")[-1]
            elif tunnel.target_vpn_gateway:
                gateway = tunnel.target_vpn_gateway.split("/")[-1]
            else:
                gateway = "unknown"
            results.append({
                "tunnel_name": tunnel.name,
                "region":      region,
                "gateway":     gateway,
                "status":      tunnel.status,
                "is_up":       tunnel.status == "ESTABLISHED",
            })
    return results


def get_interconnect_attachments(project_id: str) -> list[dict[str, Any]]:
    """Return Interconnect VLAN attachment status from all regions."""
    client  = compute_v1.InterconnectAttachmentsClient()
    results: list[dict[str, Any]] = []
    for scope, scoped_list in client.aggregated_list(project=project_id):
        if not scoped_list.interconnect_attachments:
            continue
        region = scope.replace("regions/", "")
        for att in scoped_list.interconnect_attachments:
            interconnect = att.interconnect.split("/")[-1] if att.interconnect else "none"
            results.append({
                "attachment_name": att.name,
                "region":          region,
                "interconnect":    interconnect,
                "bandwidth":       att.bandwidth or "unknown",
                "op_status":       att.operational_status or "unknown",
                "is_up":           att.operational_status == "OS_ACTIVE",
            })
    return results


# ── Metric writers ─────────────────────────────────────────────────────────────

def _now_interval() -> monitoring_v3.TimeInterval:
    now_s  = int(time.time())
    now_ns = int((time.time() - now_s) * 1e9)
    return monitoring_v3.TimeInterval(end_time={"seconds": now_s, "nanos": now_ns})


def _series(metric_type: str, labels: dict,
            interval: monitoring_v3.TimeInterval, value: int) -> monitoring_v3.TimeSeries:
    return monitoring_v3.TimeSeries(
        metric={"type": metric_type, "labels": labels},
        resource={"type": "global", "labels": {"project_id": PROJECT_ID}},
        points=[monitoring_v3.Point(
            interval=interval,
            value=monitoring_v3.TypedValue(int64_value=value),
        )],
    )


def _write(client: monitoring_v3.MetricServiceClient,
           series: list, label: str) -> None:
    if series:
        client.create_time_series(name=PROJECT_NAME, time_series=series)
        print(f"[metrics] Wrote {len(series)} {label} point(s).")


def write_spoke_metrics(client: monitoring_v3.MetricServiceClient,
                        spokes: list[dict]) -> None:
    interval     = _now_interval()
    status_series = [
        _series(METRIC_SPOKE_STATUS, {
            "spoke_name": s["spoke_name"], "hub": s["hub"],
            "location":   s["location"],   "state": s["state"],
        }, interval, 1 if s["is_active"] else 0)
        for s in spokes
    ]
    counts = collections.Counter((s["hub"], s["state"]) for s in spokes)
    count_series = [
        _series(METRIC_SPOKE_COUNT, {"hub": hub, "state": state}, interval, count)
        for (hub, state), count in counts.items()
    ]
    _write(client, status_series, "spoke_status")
    _write(client, count_series,  "spoke_count")


def write_route_metrics(client: monitoring_v3.MetricServiceClient,
                        route_data: list[dict]) -> None:
    if not route_data:
        return
    interval    = _now_interval()
    series_list = [
        _series(METRIC_ROUTE_COUNT, {
            "hub": r["hub"], "route_table": r["route_table"],
            "route_state": r["route_state"],
        }, interval, r["count"])
        for r in route_data
    ]
    _write(client, series_list, "route_count")


def write_bgp_metrics(client: monitoring_v3.MetricServiceClient,
                      peers: list[dict]) -> None:
    if not peers:
        return
    interval = _now_interval()
    def _lbl(p):
        return {"router": p["router"], "region": p["region"],
                "network": p["network"], "peer_name": p["peer_name"]}
    _write(client,
           [_series(METRIC_BGP_PEER_UP, _lbl(p), interval, 1 if p["is_up"] else 0) for p in peers],
           "bgp_peer_up")
    _write(client,
           [_series(METRIC_BGP_ROUTES, _lbl(p), interval, p["num_learned_routes"]) for p in peers],
           "bgp_learned_routes")


def write_vpn_metrics(client: monitoring_v3.MetricServiceClient,
                      tunnels: list[dict]) -> None:
    if not tunnels:
        return
    interval    = _now_interval()
    series_list = [
        _series(METRIC_VPN_TUNNEL_UP, {
            "tunnel_name": t["tunnel_name"], "region":  t["region"],
            "gateway":     t["gateway"],     "status":  t["status"],
        }, interval, 1 if t["is_up"] else 0)
        for t in tunnels
    ]
    _write(client, series_list, "vpn_tunnel_up")


def write_interconnect_metrics(client: monitoring_v3.MetricServiceClient,
                               attachments: list[dict]) -> None:
    if not attachments:
        return
    interval    = _now_interval()
    series_list = [
        _series(METRIC_INTERCONNECT_UP, {
            "attachment_name": a["attachment_name"], "region":       a["region"],
            "interconnect":    a["interconnect"],    "bandwidth":    a["bandwidth"],
            "op_status":       a["op_status"],
        }, interval, 1 if a["is_up"] else 0)
        for a in attachments
    ]
    _write(client, series_list, "interconnect_up")


# ── Dashboard helpers ──────────────────────────────────────────────────────────

def _dur(seconds: int) -> duration_pb2.Duration:
    return duration_pb2.Duration(seconds=seconds)


def _text_widget(content: str) -> dashboard_v1.Widget:
    return dashboard_v1.Widget(
        text=dashboard_v1.Text(content=content, format_=dashboard_v1.Text.Format.MARKDOWN)
    )


def _scorecard_widget(title: str, metric_filter: str) -> dashboard_v1.Widget:
    return dashboard_v1.Widget(
        title=title,
        scorecard=dashboard_v1.Scorecard(
            time_series_query=dashboard_v1.TimeSeriesQuery(
                time_series_filter=dashboard_v1.TimeSeriesFilter(
                    filter=metric_filter,
                    aggregation=dashboard_v1.Aggregation(
                        alignment_period=_dur(60),
                        per_series_aligner=dashboard_v1.Aggregation.Aligner.ALIGN_MEAN,
                        cross_series_reducer=dashboard_v1.Aggregation.Reducer.REDUCE_SUM,
                    ),
                )
            ),
            spark_chart_view=dashboard_v1.Scorecard.SparkChartView(
                spark_chart_type=dashboard_v1.SparkChartType.SPARK_LINE,
                min_alignment_period=_dur(60),
            ),
        ),
    )


def _line_chart_widget(title: str, metric_filter: str, group_by: str,
                       is_resource_label: bool = False) -> dashboard_v1.Widget:
    field = f"resource.label.{group_by}" if is_resource_label else f"metric.label.{group_by}"
    return dashboard_v1.Widget(
        title=title,
        xy_chart=dashboard_v1.XyChart(
            data_sets=[
                dashboard_v1.XyChart.DataSet(
                    time_series_query=dashboard_v1.TimeSeriesQuery(
                        time_series_filter=dashboard_v1.TimeSeriesFilter(
                            filter=metric_filter,
                            aggregation=dashboard_v1.Aggregation(
                                alignment_period=_dur(60),
                                per_series_aligner=dashboard_v1.Aggregation.Aligner.ALIGN_MEAN,
                                group_by_fields=[field],
                                cross_series_reducer=dashboard_v1.Aggregation.Reducer.REDUCE_SUM,
                            ),
                        )
                    ),
                    plot_type=dashboard_v1.XyChart.DataSet.PlotType.LINE,
                    legend_template=f"${{{field}}}",
                )
            ],
            chart_options=dashboard_v1.ChartOptions(mode=dashboard_v1.ChartOptions.Mode.COLOR),
        ),
    )


def _tile(x: int, y: int, w: int, h: int,
          widget: dashboard_v1.Widget) -> dashboard_v1.MosaicLayout.Tile:
    return dashboard_v1.MosaicLayout.Tile(x_pos=x, y_pos=y, width=w, height=h, widget=widget)


def _build_dashboard() -> dashboard_v1.Dashboard:
    # ── filter strings ──────────────────────────────────────────────────────────
    f_spoke_all    = f'metric.type="{METRIC_SPOKE_STATUS}"'
    f_spoke_active = f'metric.type="{METRIC_SPOKE_STATUS}" metric.label."state"="ACTIVE"'
    f_count_all    = f'metric.type="{METRIC_SPOKE_COUNT}"'
    f_route_all    = f'metric.type="{METRIC_ROUTE_COUNT}"'
    f_route_active = f'metric.type="{METRIC_ROUTE_COUNT}" metric.label."route_state"="ACTIVE"'
    f_bgp_up       = f'metric.type="{METRIC_BGP_PEER_UP}"'
    f_bgp_routes   = f'metric.type="{METRIC_BGP_ROUTES}"'
    f_vpn_up       = f'metric.type="{METRIC_VPN_TUNNEL_UP}"'
    f_ic_up        = f'metric.type="{METRIC_INTERCONNECT_UP}"'
    f_other_usage  = f'metric.type="{ROUTER_OTHER_USAGE}"'
    f_other_limit  = f'metric.type="{ROUTER_OTHER_LIMIT}"'
    f_own_usage    = f'metric.type="{ROUTER_OWN_USAGE}"'
    f_own_limit    = f'metric.type="{ROUTER_OWN_LIMIT}"'

    tiles = [

        # ── Section 1: NCC Spoke Health ──────────────────────────── y 0–12
        _tile(0,  0, 12, 2, _text_widget(
            "## NCC Spoke Health\n"
            "Custom metrics from the NCC API. A spoke can show ACTIVE in NCC "
            "briefly after an underlying link drops — watch Section 3 (Link Health) "
            "in conjunction with this section."
        )),
        _tile(0,  2,  3, 3, _scorecard_widget("Active Spokes",          f_spoke_active)),
        _tile(3,  2,  3, 3, _scorecard_widget("Total Spokes Monitored", f_spoke_all)),
        _tile(0,  5,  6, 4, _line_chart_widget("Spoke Status per Spoke (1=Active)", f_spoke_all, "spoke_name")),
        _tile(6,  5,  6, 4, _line_chart_widget("Spoke Status by Location",          f_spoke_all, "location")),
        _tile(0,  9,  6, 4, _line_chart_widget("Spoke Count by State",              f_count_all, "state")),
        _tile(6,  9,  6, 4, _line_chart_widget("Spoke Count by Hub",                f_count_all, "hub")),

        # ── Section 2: NCC Route Inventory ───────────────────────── y 13–26
        _tile(0, 13, 12, 2, _text_widget(
            "## NCC Route Inventory\n"
            "Custom metric from the NCC route-table API — the **control-plane** view "
            "of how many routes NCC has installed in each hub route table."
        )),
        _tile(0, 15,  3, 3, _scorecard_widget("Active NCC Routes", f_route_active)),
        _tile(3, 15,  3, 3, _scorecard_widget("Total NCC Routes",   f_route_all)),
        _tile(0, 18, 12, 4, _line_chart_widget("Route Count by Route Table", f_route_all,    "route_table")),
        _tile(0, 22,  6, 4, _line_chart_widget("Active Routes by Hub",       f_route_active, "hub")),
        _tile(6, 22,  6, 4, _line_chart_widget("Route Count by State",        f_route_all,    "route_state")),

        # ── Section 3: Link Health ────────────────────────────────── y 26–61
        _tile(0, 26, 12, 3, _text_widget(
            "## Link Health — BGP Sessions · VPN Tunnels · Interconnect Attachments\n"
            "Custom metrics written each run by polling the Compute API. These are the "
            "**data-plane** signals. A drop here before NCC spoke state changes means "
            "the underlay failed before NCC has converged.\n\n"
            "**BGP:** polled from `routers.getStatus()` — 1 = UP, 0 = DOWN. "
            "`bgp_learned_routes` dropping to 0 from an active peer is a propagation red flag. "
            "**VPN:** 1 = ESTABLISHED. **Interconnect:** 1 = OS\\_ACTIVE."
        )),

        # BGP ──────────────────────────────────────────────────────── y 29–43
        _tile(0, 29,  3, 3, _scorecard_widget("BGP Peers Up",            f_bgp_up)),
        _tile(3, 29,  3, 3, _scorecard_widget("BGP Routes Learned",      f_bgp_routes)),
        _tile(0, 32,  6, 4, _line_chart_widget("BGP Peer Up per Peer (1=Up, 0=Down)",   f_bgp_up,     "peer_name")),
        _tile(6, 32,  6, 4, _line_chart_widget("BGP Peer Up per Router",                f_bgp_up,     "router")),
        _tile(0, 36,  6, 4, _line_chart_widget("BGP Learned Routes per Peer",           f_bgp_routes, "peer_name")),
        _tile(6, 36,  6, 4, _line_chart_widget("BGP Learned Routes per Router",         f_bgp_routes, "router")),

        # VPN ──────────────────────────────────────────────────────── y 40–54
        _tile(0, 40,  3, 3, _scorecard_widget("VPN Tunnels Up", f_vpn_up)),
        _tile(0, 43, 12, 4, _line_chart_widget("VPN Tunnel Status per Tunnel (1=Established)", f_vpn_up, "tunnel_name")),
        _tile(0, 47,  6, 4, _line_chart_widget("VPN Tunnel Status by Gateway",                 f_vpn_up, "gateway")),
        _tile(6, 47,  6, 4, _line_chart_widget("VPN Tunnel Status by Region",                  f_vpn_up, "region")),

        # Interconnect ─────────────────────────────────────────────── y 51–61
        _tile(0, 51,  3, 3, _scorecard_widget("Interconnect Attachments Up", f_ic_up)),
        _tile(0, 54, 12, 4, _line_chart_widget("Interconnect Attachment Status (1=OS_Active)", f_ic_up, "attachment_name")),
        _tile(0, 58,  6, 4, _line_chart_widget("Attachment Status by Interconnect",            f_ic_up, "interconnect")),
        _tile(6, 58,  6, 4, _line_chart_widget("Attachment Status by Region",                  f_ic_up, "region")),

        # ── Section 4: Cloud Router Prefix Quotas ────────────────── y 62–73
        _tile(0, 62, 12, 3, _text_widget(
            "## Cloud Router Prefix Quotas — Route Propagation Proxy\n"
            "GCP platform quota metrics (per VPC network per region). A sudden drop "
            "toward zero means routes stopped propagating through NCC. Approaching "
            "the limit risks quota-based route withdrawal.\n\n"
            "Note: `compute.googleapis.com/router/bgp/*` per-session metrics are only "
            "emitted for Dedicated/Partner Interconnect — not for HA VPN or NCC VPN spokes."
        )),
        _tile(0, 65,  6, 4, _line_chart_widget("Cross-Region Prefixes Learned", f_other_usage, "network_id", is_resource_label=True)),
        _tile(6, 65,  6, 4, _line_chart_widget("Cross-Region Prefixes Limit",   f_other_limit, "network_id", is_resource_label=True)),
        _tile(0, 69,  6, 4, _line_chart_widget("Own-Region Prefixes Learned",   f_own_usage,   "network_id", is_resource_label=True)),
        _tile(6, 69,  6, 4, _line_chart_widget("Own-Region Prefixes Limit",     f_own_limit,   "network_id", is_resource_label=True)),
    ]

    return dashboard_v1.Dashboard(
        display_name=DASHBOARD_DISPLAY_NAME,
        mosaic_layout=dashboard_v1.MosaicLayout(columns=12, tiles=tiles),
    )


def ensure_dashboard(dash_client: dashboard_v1.DashboardsServiceClient) -> str:
    dashboard = _build_dashboard()
    for existing in dash_client.list_dashboards(parent=PROJECT_NAME):
        if existing.display_name == DASHBOARD_DISPLAY_NAME:
            dashboard.name = existing.name
            dashboard.etag = existing.etag
            updated = dash_client.update_dashboard(
                request=dashboard_v1.UpdateDashboardRequest(dashboard=dashboard)
            )
            print(f"[dashboard] Updated: {updated.name}")
            return updated.name
    created = dash_client.create_dashboard(parent=PROJECT_NAME, dashboard=dashboard)
    print(f"[dashboard] Created: {created.name}")
    return created.name


# ── Entrypoint ─────────────────────────────────────────────────────────────────

def main() -> None:
    metric_client = monitoring_v3.MetricServiceClient()
    dash_client   = dashboard_v1.DashboardsServiceClient()

    print("=== NCC Comprehensive Monitor ===\n")
    create_metric_descriptors(metric_client)

    # ── NCC spokes
    print()
    spokes = get_spokes(PROJECT_ID)
    if not spokes:
        print("[ncc] No spokes found.")
    else:
        print(f"[ncc] Found {len(spokes)} spoke(s):")
        for s in spokes:
            flag = "✓" if s["is_active"] else "✗"
            print(f"  {flag} {s['spoke_name']:<30} type={s['spoke_type']:<15} "
                  f"location={s['location']:<20} hub={s['hub']:<20} state={s['state']}")
        print()
        write_spoke_metrics(metric_client, spokes)

    # ── NCC routes
    print("\n[ncc] Fetching route tables...")
    route_data = get_route_counts(PROJECT_ID)
    if route_data:
        for r in route_data:
            print(f"  hub={r['hub']:<20} table={r['route_table']:<20} "
                  f"state={r['route_state']:<12} count={r['count']}")
        write_route_metrics(metric_client, route_data)
    else:
        print("[ncc] No route tables found.")

    # ── BGP peers
    print("\n[compute] Fetching BGP peer status...")
    bgp_peers = get_bgp_peers(PROJECT_ID)
    if bgp_peers:
        for p in bgp_peers:
            flag = "✓" if p["is_up"] else "✗"
            print(f"  {flag} {p['router']:<20} peer={p['peer_name']:<25} "
                  f"status={p['status']:<5} routes={p['num_learned_routes']}")
        write_bgp_metrics(metric_client, bgp_peers)
    else:
        print("[compute] No BGP peers found.")

    # ── VPN tunnels
    print("\n[compute] Fetching VPN tunnel status...")
    tunnels = get_vpn_tunnels(PROJECT_ID)
    if tunnels:
        for t in tunnels:
            flag = "✓" if t["is_up"] else "✗"
            print(f"  {flag} {t['tunnel_name']:<30} gateway={t['gateway']:<25} "
                  f"region={t['region']:<20} status={t['status']}")
        write_vpn_metrics(metric_client, tunnels)
    else:
        print("[compute] No VPN tunnels found.")

    # ── Interconnect attachments
    print("\n[compute] Fetching Interconnect attachment status...")
    attachments = get_interconnect_attachments(PROJECT_ID)
    if attachments:
        for a in attachments:
            flag = "✓" if a["is_up"] else "✗"
            print(f"  {flag} {a['attachment_name']:<30} region={a['region']:<20} "
                  f"interconnect={a['interconnect']:<20} status={a['op_status']}")
        write_interconnect_metrics(metric_client, attachments)
    else:
        print("[compute] No Interconnect attachments found.")

    # ── Dashboard
    print()
    dashboard_name = ensure_dashboard(dash_client)
    dashboard_id   = dashboard_name.split("/")[-1]
    print(
        f"\n[dashboard] View at:\n"
        f"  https://console.cloud.google.com/monitoring/dashboards/custom/"
        f"{dashboard_id}?project={PROJECT_ID}"
    )


if __name__ == "__main__":
    main()
