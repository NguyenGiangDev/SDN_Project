# Copyright (C) 2016 Nippon Telegraph and Telephone Corporation.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.


#CODE MẪU THỬ NGHIỆM KHI CHƯA ÁP DỤNG QOS.
from operator import attrgetter
import time
from prometheus_client import Gauge, start_http_server
from ryu.app import simple_switch_13
from ryu.controller import ofp_event
from ryu.controller.handler import MAIN_DISPATCHER, DEAD_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.lib import hub


class AdaptiveMonitor13(simple_switch_13.SimpleSwitch13):  

    def __init__(self, *args, **kwargs):
        super(AdaptiveMonitor13, self).__init__(*args, **kwargs)
        self.datapaths = {}
        
        # Store byte counts for each port on each datapath
        self.port_stats = {}
        
        # Store last poll time for each datapath
        self.last_poll_time = {}
        
        # Monitoring parameters
        self.DEFAULT_INTERVAL = 10  # thời gian lặp mặc định
        self.MIN_INTERVAL = 1       # thời gian lặp khi phát hiện high traffic
        self.THRESHOLD_BYTES_PER_SEC = 1000000  # 1 Mbps threshold
        
        # Current polling interval
        self.poll_interval = self.DEFAULT_INTERVAL
        
        # Start the monitoring thread
        self.monitor_thread = hub.spawn(self._monitor)
        self.port_traffic_gauge = Gauge(
            'ryu_port_bytes_per_sec', 
            'Port traffic in bytes per second',
            ['dpid', 'port']
        )
        # Start the monitoring thread
        self.monitor_thread = hub.spawn(self._monitor)
        start_http_server(9000)
    @set_ev_cls(ofp_event.EventOFPStateChange,
                [MAIN_DISPATCHER, DEAD_DISPATCHER])
    def _state_change_handler(self, ev):
        datapath = ev.datapath
        if ev.state == MAIN_DISPATCHER:
            if datapath.id not in self.datapaths:
                self.logger.debug('register datapath: %016x', datapath.id)
                self.datapaths[datapath.id] = datapath
                self.port_stats[datapath.id] = {}
                self.last_poll_time[datapath.id] = time.time()
        elif ev.state == DEAD_DISPATCHER:
            if datapath.id in self.datapaths:
                self.logger.debug('unregister datapath: %016x', datapath.id)
                del self.datapaths[datapath.id]
                if datapath.id in self.port_stats:
                    del self.port_stats[datapath.id]
                if datapath.id in self.last_poll_time:
                    del self.last_poll_time[datapath.id]

    def _monitor(self):
        while True:
            current_time = time.time()
            max_bytes_per_sec = 0
            
            for dp in self.datapaths.values():
               
                if current_time - self.last_poll_time[dp.id] >= self.poll_interval:
                    self._request_stats(dp)
                    self.last_poll_time[dp.id] = current_time
            
          
            hub.sleep(1)  # Check every second if any datapath needs polling

    def _request_stats(self, datapath):
        self.logger.debug('send stats request: %016x', datapath.id)
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        req = parser.OFPFlowStatsRequest(datapath)
        datapath.send_msg(req)

        req = parser.OFPPortStatsRequest(datapath, 0, ofproto.OFPP_ANY)
        datapath.send_msg(req)

    @set_ev_cls(ofp_event.EventOFPFlowStatsReply, MAIN_DISPATCHER)
    def _flow_stats_reply_handler(self, ev):
        body = ev.msg.body

        self.logger.info('datapath         '
                         'in-port  eth-dst           '
                         'out-port packets  bytes')
        self.logger.info('---------------- '
                         '-------- ----------------- '
                         '-------- -------- --------')
        for stat in sorted([flow for flow in body if flow.priority == 1],
                           key=lambda flow: (flow.match['in_port'],
                                             flow.match['eth_dst'])):
            self.logger.info('%016x %8x %17s %8x %8d %8d',
                             ev.msg.datapath.id,
                             stat.match['in_port'], stat.match['eth_dst'],
                             stat.instructions[0].actions[0].port,
                             stat.packet_count, stat.byte_count)

    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def _port_stats_reply_handler(self, ev):
        body = ev.msg.body
        datapath_id = ev.msg.datapath.id
        current_time = time.time()
        max_bytes_per_sec = 0

        self.logger.info('datapath         port     '
                         'rx-pkts  rx-bytes rx-error '
                         'tx-pkts  tx-bytes tx-error  bytes/sec')
        self.logger.info('---------------- -------- '
                         '-------- -------- -------- '
                         '-------- -------- --------  --------')
        
        for stat in sorted(body, key=attrgetter('port_no')):
            port_no = stat.port_no
            
            # Tính bytes/sec 
            bytes_per_sec = 0
            if datapath_id in self.port_stats and port_no in self.port_stats[datapath_id]:
                prev_bytes = self.port_stats[datapath_id][port_no]['rx_bytes'] + self.port_stats[datapath_id][port_no]['tx_bytes']
                curr_bytes = stat.rx_bytes + stat.tx_bytes
                time_diff = current_time - self.port_stats[datapath_id][port_no]['timestamp']
                
                if time_diff > 0:  # Avoid division by zero
                    bytes_per_sec = (curr_bytes - prev_bytes) / time_diff
                    
                # Track the maximum bytes/sec across all ports
                max_bytes_per_sec = max(max_bytes_per_sec, bytes_per_sec)
                # Cập nhật dữ liệu cho Prometheus
                self.port_traffic_gauge.labels(dpid=str(datapath_id), port=str(port_no)).set(bytes_per_sec)
            
            # Lưu stat
            if datapath_id not in self.port_stats:
                self.port_stats[datapath_id] = {}
            
            self.port_stats[datapath_id][port_no] = {
                'rx_bytes': stat.rx_bytes,
                'tx_bytes': stat.tx_bytes,
                'timestamp': current_time
            }
            
            self.logger.info('%016x %8x %8d %8d %8d %8d %8d %8d  %8d',
                             datapath_id, stat.port_no,
                             stat.rx_packets, stat.rx_bytes, stat.rx_errors,
                             stat.tx_packets, stat.tx_bytes, stat.tx_errors,
                             int(bytes_per_sec))
        
   
        if max_bytes_per_sec > self.THRESHOLD_BYTES_PER_SEC:
            if self.poll_interval > self.MIN_INTERVAL:
                self.poll_interval = self.MIN_INTERVAL
                self.logger.info('High traffic detected (%d bytes/sec)! Decreasing polling interval to %d seconds',
                                int(max_bytes_per_sec), self.poll_interval)
        else:
            if self.poll_interval < self.DEFAULT_INTERVAL:
                self.poll_interval = self.DEFAULT_INTERVAL
                self.logger.info('Traffic back to normal (%d bytes/sec). Resetting polling interval to %d seconds',
                                int(max_bytes_per_sec), self.poll_interval)