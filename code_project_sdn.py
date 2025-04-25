from operator import attrgetter
import time
from prometheus_client import Gauge, start_http_server
from ryu.app import simple_switch_13
from ryu.controller import ofp_event
from ryu.controller.handler import MAIN_DISPATCHER, DEAD_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.lib import hub
import smtplib
from email.mime.text import MIMEText

def send_alert_email(dpid, port, traffic):
        import smtplib
        from email.mime.text import MIMEText

        sender = "nguyengiangdevw@gmail.com"
        receiver = "22520358@gm.uit.edu.vn"
        password = "wifv wmbp toki igqq" 

        subject = f"[CẢNH BÁO] Lưu lượng cao tại Switch {dpid}, Port {port}"
        body = f"""
        ❗ CẢNH BÁO LƯU LƯỢNG CAO ❗

        Switch: {dpid}
        Port: {port}
        Traffic: {traffic:.2f} bytes/sec

        Hệ thống giám sát phát hiện lưu lượng bất thường. 
        Vui lòng kiểm tra ngay.
        """

        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = sender
        msg["To"] = receiver

        try:
            server = smtplib.SMTP_SSL("smtp.gmail.com", 465)
            server.login(sender, password)
            server.sendmail(sender, receiver, msg.as_string())
            server.quit()
            print("✅ Đã gửi email cảnh báo.")
        except Exception as e:
            print(f"❌ Lỗi khi gửi email: {e}")


class AdaptiveMonitor13(simple_switch_13.SimpleSwitch13):

    def __init__(self, *args, **kwargs):
        super(AdaptiveMonitor13, self).__init__(*args, **kwargs)
        self.datapaths = {}
        
       
        self.port_stats = {}
        
      
        self.last_poll_time = {}
        
       
        self.DEFAULT_INTERVAL = 10  # thời gian lặp mặc định
        self.MIN_INTERVAL = 1       # thời gian lặp khi phát hiện high traffic
        self.THRESHOLD_BYTES_PER_SEC = 1000000  # 1 Mbps threshold
        
     
        self.poll_interval = self.DEFAULT_INTERVAL
        self.port_traffic_gauge = Gauge(
            'ryu_port_bytes_per_sec', 
            'Port traffic in bytes per second',
            ['dpid', 'port']
        )
        
        self.monitor_thread = hub.spawn(self._monitor)
        start_http_server(9000)
        self.alert_status = {} 
        self.alert_duration = 5  # giây
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
            hub.sleep(1)  

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
            key = f"{datapath_id}_{port_no}"

            # Tính bytes/sec
            bytes_per_sec = 0
            if datapath_id in self.port_stats and port_no in self.port_stats[datapath_id]:
                prev_rx = self.port_stats[datapath_id][port_no]['rx_bytes']
                prev_tx = self.port_stats[datapath_id][port_no]['tx_bytes']
                prev_time = self.port_stats[datapath_id][port_no]['timestamp']
                time_diff = current_time - prev_time

                if time_diff > 0:
                    curr_bytes = stat.rx_bytes + stat.tx_bytes
                    prev_bytes = prev_rx + prev_tx
                    bytes_per_sec = (curr_bytes - prev_bytes) / time_diff

                max_bytes_per_sec = max(max_bytes_per_sec, bytes_per_sec)

            # Cập nhật dữ liệu cho Prometheus
            self.port_traffic_gauge.labels(dpid=str(datapath_id), port=str(port_no)).set(bytes_per_sec)

            # Cập nhật dữ liệu theo dõi
            if datapath_id not in self.port_stats:
                self.port_stats[datapath_id] = {}
            self.port_stats[datapath_id][port_no] = {
                'rx_bytes': stat.rx_bytes,
                'tx_bytes': stat.tx_bytes,
                'timestamp': current_time
            }

            self.logger.info('%016x %8x %8d %8d %8d %8d %8d %8d  %8d',
                            datapath_id, port_no,
                            stat.rx_packets, stat.rx_bytes, stat.rx_errors,
                            stat.tx_packets, stat.tx_bytes, stat.tx_errors,
                            int(bytes_per_sec))

            # Giám sát và gửi email nếu vượt ngưỡng NGAY LẬP TỨC
            if bytes_per_sec > self.THRESHOLD_BYTES_PER_SEC:
                if key not in self.alert_status or current_time - self.alert_status[key] > 60:
                    send_alert_email(datapath_id, port_no, bytes_per_sec)
                    self.logger.info('[EMAIL ALERT] HIGH TRAFFIC DETECTED IMMEDIATELY!')
                    self.alert_status[key] = current_time  # Cập nhật thời gian gửi cảnh báo
            else:
                if key in self.alert_status:
                    del self.alert_status[key]

        # QoS dynamic logic
        if max_bytes_per_sec > self.THRESHOLD_BYTES_PER_SEC:
            if self.poll_interval > self.MIN_INTERVAL:
                self.poll_interval = self.MIN_INTERVAL
                self.logger.info('❌ High traffic detected (%d bytes/sec)! Decreasing polling interval to %d seconds',
                                int(max_bytes_per_sec), self.poll_interval)
                self._apply_qos_policy(datapath_id)
                self.qos_removed = False
        else:
            if self.poll_interval < self.DEFAULT_INTERVAL and not self.qos_removed:
                self.poll_interval = self.DEFAULT_INTERVAL
                self.logger.info('✅ Traffic back to normal (%d bytes/sec). Resetting polling interval to %d seconds in 15s...',
                                int(max_bytes_per_sec), self.poll_interval)
                hub.spawn(self._delayed_qos_removal, datapath_id)


    # Hàm phụ trợ
    def _delayed_qos_removal(self, datapath_id):
        hub.sleep(15)  # Đợi 15 giây
        self.logger.info('🔁 Removing QoS policy for datapath %016x after delay', datapath_id)
        self._remove_qos_policy(datapath_id)
        self.qos_removed = True  # Đánh dấu đã gỡ

    def _apply_qos_policy(self, datapath_id):
        datapath = self.datapaths.get(datapath_id)
        if not datapath:
            return

        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

     
        port_map = {
            's1': {1: 2, 2: 1},  # switch s1
            's2': {1: 3, 3: 2},  # switch s2
            's3': {1: 4, 4: 3},  # switch s3
            's4': {2: 1},        # switch s4 (có 1 kết nối với s3)
        }

        for switch, ports in port_map.items():
            for in_port, out_port in ports.items():
                match = parser.OFPMatch(in_port=in_port)
                actions = [
                    parser.OFPActionSetQueue(1),
                    parser.OFPActionOutput(out_port)
                ]
                instructions = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
                mod = parser.OFPFlowMod(
                    datapath=datapath,
                    priority=100,
                    match=match,
                    instructions=instructions,
                    idle_timeout=10,
                    hard_timeout=30
                )
                datapath.send_msg(mod)
                self.logger.info("📊 QoS applied: switch %s, in_port %d → out_port %d", switch, in_port, out_port)



    def _remove_qos_policy(self, datapath_id):
        datapath = self.datapaths.get(datapath_id)
        if not datapath:
            return

        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        match = parser.OFPMatch()  # Xoá tất cả flow
        mod = parser.OFPFlowMod(
            datapath=datapath,
            match=match,
            command=ofproto.OFPFC_DELETE,
            out_port=ofproto.OFPP_ANY,
            out_group=ofproto.OFPG_ANY
        )
        datapath.send_msg(mod)
        self.logger.info("✅ QoS policy removed from datapath %016x", datapath_id)

  