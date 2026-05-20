import socket
import time
from typing import Any, Dict, List, Optional, Tuple

import requests


DEFAULT_NETWORK_CONFIG: Dict[str, Any] = {
    "enabled": True,
    "testUrls": [
        "https://www.feishu.cn",
        "https://www.baidu.com",
    ],
    "dnsHosts": [
        "open.feishu.cn",
        "www.baidu.com",
    ],
    "tcpTargets": [
        ["223.5.5.5", 53],
        ["114.114.114.114", 53],
        ["open.feishu.cn", 443],
    ],
    "sampleCount": 3,
    "timeoutSeconds": 3,
    "maxLatencyMs": 1500,
    "maxJitterMs": 800,
    "minSuccessRate": 0.7,
}


class NetworkDiagnostics:
    def __init__(self, config: Dict[str, Any]) -> None:
        self.config = config or DEFAULT_NETWORK_CONFIG

    def run(self) -> Tuple[Optional[float], Dict[str, Any]]:
        if not self.config.get("enabled", True):
            return None, {
                "status": "disabled",
                "title": "网络检测未启用",
                "reason": "network.enabled=false",
                "suggestion": "无需处理。",
            }

        urls = [str(x) for x in self.config.get("testUrls", [])]
        dns_hosts = [str(x) for x in self.config.get("dnsHosts", [])]
        tcp_targets = self.config.get("tcpTargets", [])

        sample_count = max(1, int(self.config.get("sampleCount", 3)))
        timeout = float(self.config.get("timeoutSeconds", 3))
        max_latency_ms = float(self.config.get("maxLatencyMs", 1500))
        max_jitter_ms = float(self.config.get("maxJitterMs", 800))
        min_success_rate = float(self.config.get("minSuccessRate", 0.7))

        if not urls and not dns_hosts and not tcp_targets:
            return None, {
                "status": "disabled",
                "title": "网络检测未配置",
                "reason": "未配置 testUrls / dnsHosts / tcpTargets。",
                "suggestion": "请在 guardian_config.json 中配置 network 检测项。",
            }

        dns_results = self.dns_check(dns_hosts, timeout) if dns_hosts else []
        tcp_results = self.tcp_check(tcp_targets, timeout) if tcp_targets else []
        http_results = self.http_check(urls, sample_count, timeout) if urls else []

        return self.classify(
            dns_results=dns_results,
            tcp_results=tcp_results,
            http_results=http_results,
            max_latency_ms=max_latency_ms,
            max_jitter_ms=max_jitter_ms,
            min_success_rate=min_success_rate,
        )

    def dns_check(self, hosts: List[str], timeout: float) -> List[Dict[str, Any]]:
        results = []
        old_timeout = socket.getdefaulttimeout()
        socket.setdefaulttimeout(timeout)

        try:
            for host in hosts:
                started = time.time()
                try:
                    infos = socket.getaddrinfo(host, None)
                    latency_ms = round((time.time() - started) * 1000, 2)
                    addresses = sorted({item[4][0] for item in infos if item and item[4]})

                    results.append(
                        {
                            "host": host,
                            "ok": True,
                            "latency_ms": latency_ms,
                            "addresses": addresses[:5],
                            "error": "",
                        }
                    )
                except Exception as exc:
                    latency_ms = round((time.time() - started) * 1000, 2)
                    results.append(
                        {
                            "host": host,
                            "ok": False,
                            "latency_ms": latency_ms,
                            "addresses": [],
                            "error": str(exc),
                        }
                    )
        finally:
            socket.setdefaulttimeout(old_timeout)

        return results

    def tcp_check(self, targets: List[List[Any]], timeout: float) -> List[Dict[str, Any]]:
        results = []

        for item in targets:
            if not item or len(item) < 2:
                continue

            host = str(item[0])
            port = int(item[1])
            started = time.time()

            try:
                with socket.create_connection((host, port), timeout=timeout):
                    latency_ms = round((time.time() - started) * 1000, 2)
                    results.append(
                        {
                            "host": host,
                            "port": port,
                            "ok": True,
                            "latency_ms": latency_ms,
                            "error": "",
                        }
                    )
            except Exception as exc:
                latency_ms = round((time.time() - started) * 1000, 2)
                results.append(
                    {
                        "host": host,
                        "port": port,
                        "ok": False,
                        "latency_ms": latency_ms,
                        "error": str(exc),
                    }
                )

        return results

    def http_check(
        self,
        urls: List[str],
        sample_count: int,
        timeout: float,
    ) -> List[Dict[str, Any]]:
        results = []

        for url in urls:
            for sample_index in range(sample_count):
                started = time.time()

                try:
                    response = requests.get(url, timeout=timeout)
                    latency_ms = round((time.time() - started) * 1000, 2)
                    ok = response.status_code < 500

                    results.append(
                        {
                            "url": url,
                            "sample": sample_index + 1,
                            "ok": ok,
                            "status_code": response.status_code,
                            "latency_ms": latency_ms,
                            "error": "",
                        }
                    )
                except Exception as exc:
                    latency_ms = round((time.time() - started) * 1000, 2)

                    results.append(
                        {
                            "url": url,
                            "sample": sample_index + 1,
                            "ok": False,
                            "status_code": None,
                            "latency_ms": latency_ms,
                            "error": str(exc),
                        }
                    )

        return results

    def classify(
        self,
        dns_results: List[Dict[str, Any]],
        tcp_results: List[Dict[str, Any]],
        http_results: List[Dict[str, Any]],
        max_latency_ms: float,
        max_jitter_ms: float,
        min_success_rate: float,
    ) -> Tuple[float, Dict[str, Any]]:
        dns_ok_count = sum(1 for r in dns_results if r.get("ok"))
        tcp_ok_count = sum(1 for r in tcp_results if r.get("ok"))
        http_ok_results = [r for r in http_results if r.get("ok")]

        http_total = len(http_results)
        http_success_count = len(http_ok_results)
        success_rate = round(http_success_count / http_total, 4) if http_total else 0.0

        latencies = [float(r["latency_ms"]) for r in http_ok_results]

        avg_latency_ms = round(sum(latencies) / len(latencies), 2) if latencies else None
        best_latency_ms = round(min(latencies), 2) if latencies else None
        worst_latency_ms = round(max(latencies), 2) if latencies else None
        jitter_ms = round(max(latencies) - min(latencies), 2) if len(latencies) >= 2 else 0.0

        details: Dict[str, Any] = {
            "dns_ok_count": dns_ok_count,
            "dns_total": len(dns_results),
            "tcp_ok_count": tcp_ok_count,
            "tcp_total": len(tcp_results),
            "http_success_count": http_success_count,
            "http_total": http_total,
            "success_rate": success_rate,
            "avg_latency_ms": avg_latency_ms,
            "best_latency_ms": best_latency_ms,
            "worst_latency_ms": worst_latency_ms,
            "jitter_ms": jitter_ms,
            "max_latency_ms": max_latency_ms,
            "max_jitter_ms": max_jitter_ms,
            "min_success_rate": min_success_rate,
            "dns_results": dns_results,
            "tcp_results": tcp_results,
            "http_results": http_results,
        }

        if tcp_ok_count == 0 and http_success_count == 0:
            details.update(
                {
                    "status": "down",
                    "title": "网络不可用",
                    "reason": "TCP 连接和 HTTP 请求均失败，当前设备大概率无法访问外网。",
                    "suggestion": "建议检查 Wi-Fi 是否断开、网线是否松动、路由器是否异常，或切换手机热点测试。",
                }
            )
            return 100.0, details

        if dns_results and dns_ok_count == 0 and tcp_ok_count > 0:
            details.update(
                {
                    "status": "dns_error",
                    "title": "DNS 异常",
                    "reason": "DNS 解析失败，但部分 TCP 连接正常，说明网络链路可能还在，域名解析可能异常。",
                    "suggestion": "建议刷新 DNS 缓存，检查 DNS、代理、VPN 或安全软件设置。",
                }
            )
            return 100.0, details

        if http_total > 0 and http_success_count == 0 and tcp_ok_count > 0:
            details.update(
                {
                    "status": "http_error",
                    "title": "HTTP 访问异常",
                    "reason": "TCP 连接存在成功记录，但 HTTP 请求全部失败，可能是代理、VPN、防火墙或目标服务访问异常。",
                    "suggestion": "建议检查代理、VPN、防火墙，或尝试访问其他网站确认是否为局部故障。",
                }
            )
            return 100.0, details

        if success_rate < min_success_rate:
            details.update(
                {
                    "status": "unstable",
                    "title": "网络波动明显",
                    "reason": f"HTTP 请求成功率偏低，当前成功率 {round(success_rate * 100, 2)}%。",
                    "suggestion": "建议检查 Wi-Fi 信号、路由器、代理/VPN，或确认是否有下载、网盘、游戏更新等程序占满带宽。",
                }
            )
            return 100.0, details

        if avg_latency_ms is not None and avg_latency_ms > max_latency_ms:
            details.update(
                {
                    "status": "slow",
                    "title": "网络延迟过高",
                    "reason": f"平均延迟 {avg_latency_ms}ms，超过阈值 {max_latency_ms}ms。",
                    "suggestion": "建议检查当前网络拥塞情况、Wi-Fi 信号强度，或关闭占用带宽的下载/同步程序。",
                }
            )
            return 100.0, details

        if jitter_ms > max_jitter_ms:
            details.update(
                {
                    "status": "unstable",
                    "title": "网络抖动明显",
                    "reason": f"延迟波动 {jitter_ms}ms，超过阈值 {max_jitter_ms}ms。",
                    "suggestion": "建议靠近路由器、切换网络、关闭代理/VPN，或检查是否有程序占满带宽。",
                }
            )
            return 100.0, details

        details.update(
            {
                "status": "ok",
                "title": "网络正常",
                "reason": "DNS、TCP 和 HTTP 检测未发现明显异常。",
                "suggestion": "无需处理。",
            }
        )
        return 0.0, details