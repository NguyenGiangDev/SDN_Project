from mininet.topo import Topo
from mininet.net import Mininet
from mininet.node import RemoteController, OVSSwitch
from mininet.cli import CLI
from mininet.link import TCLink
from mininet.log import setLogLevel

class CustomSDNTopo(Topo):
    def build(self):
        # Tạo các switch
        s1 = self.addSwitch('s1')
        s2 = self.addSwitch('s2')
        s3 = self.addSwitch('s3')
        s4 = self.addSwitch('s4')

        # Thêm host vào các switch
        for i in range(1, 5):
            self.addLink(self.addHost(f'h{i}'), s1)
        for i in range(5, 9):
            self.addLink(self.addHost(f'h{i}'), s2)
        for i in range(9, 13):
            self.addLink(self.addHost(f'h{i}'), s3)
        for i in range(13, 17):
            self.addLink(self.addHost(f'h{i}'), s4)

        # Thêm liên kết giữa các switch
        self.addLink(s1, s2)
        self.addLink(s2, s3)
        self.addLink(s3, s4)

def config_qos(net):
    switches = ['s1', 's2', 's3', 's4']
    for sw in switches:
        for i in range(1, 7):  # eth1 đến eth6 (giả định mỗi switch có tối đa 6 cổng)
            intf = f"{sw}-eth{i}"
            cmd = (
                f"ovs-vsctl -- set Port {intf} qos=@newqos "
                f"-- --id=@newqos create QoS type=linux-htb other-config:max-rate=100000000 "
                f"queues:1=@q1 -- --id=@q1 create Queue other-config:min-rate=5000000 other-config:max-rate=10000000"
            )
            net.get(sw).cmd(cmd)

def run():
    topo = CustomSDNTopo()
    net = Mininet(topo=topo, controller=None, switch=OVSSwitch, link=TCLink)
    net.addController('c0', controller=RemoteController, ip='172.25.101.47', port=6653)
    net.start()
    config_qos(net)  # 👉 Gọi cấu hình QoS tự động
    CLI(net)
    net.stop()

if __name__ == '__main__':
    setLogLevel('info')
    run()
