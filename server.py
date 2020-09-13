from twisted.protocols import basic
from twisted.internet import protocol, reactor, endpoints
from twisted.web.static import File
import datetime
import numpy
from collections import deque
from klein import Klein, url_for
from twisted.web.server import Site
import logging.handlers
from logging import Handler
import re
import json
import uuid

g_logging = deque(maxlen=30)

class RotatingHandler(Handler):
    def __init__(self):
        super().__init__()
    def emit(self, s):
        global g_logging
        try:
            ss = self.format(s).split(' | ')
            g_logging.append({'log':ss[3],'ip': ss[1], 'name': ss[2], 'timestamp':datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S.%f')})
        except:
            pass

g_trace = logging.getLogger('bot')
g_trace.setLevel(logging.DEBUG)
handler = logging.handlers.SysLogHandler(address = '/dev/log')
formatter = logging.Formatter('BOT | %(message)s')
handler.setFormatter(formatter)
g_trace.addHandler(handler)
handler = RotatingHandler()
handler.setFormatter(formatter)
g_trace.addHandler(handler)

app = Klein()
app.url_map.strict_slashes = False

colours = ['red','green','blue','brown','purple','orange','olive','aqua','navy','teal','fuchsia','maroon','aquamarine','lime','coral','crimson','indigo','lavender','salmon','turquoise']

def diff(t1,t2):
    return (t2-t1).seconds + (t2-t1).microseconds * 1e-6

@app.route('/',branch=False)
def static(request):
    return File("./static")

@app.route('/favicon.ico',branch=False)
def static1(request):
    return File("./static/favicon.ico")

@app.route('/robot.png',branch=False)
def static2(request):
    return File("./static/robot.png")

@app.route('/logging',branch=False)
def static3(request):
    return File("./static/logging.html")

@app.route('/examples/',branch=True)
def static4(request):
    return File("./examples/")

@app.route('/data', methods=['POST'])
def data(request):
    out = 'id,name,ip,batt,x,y,ammo,type\n'
    for bot in factory.arena.bots.store:
        if bot is None: continue
        pos,battery = bot.currentState()
        out += colours[bot.id].upper() + ',' + bot.name.upper() + ',' + str(bot.ip) + ',' + str(int(round(battery*20)*5))+'%' + ',' + str(pos[0]) + ',' + str(pos[1]) + ',' + str(bot.ammo) + ',bot\n'
    for _ in factory.arena.ammo.store:
        if _ is None: continue
        out += str(_.id)+',,,,'+ str(_.pos[0]) + ',' + str(_.pos[1]) + ',,ammo\n'
    return out

@app.route('/logs', methods=['POST'])
def logs(request):
    global g_logging
    out = 'timestamp,ip,name,log\n'
    for _ in g_logging:
        out += _['timestamp'] + ',' + _['ip'] + ',' +  _['name'] + ',' + _['log'] + '\n'
    return out

class Ammo():
    def __init__(self):
        self.pos = numpy.random.rand(2)
        self.id = None

class Pool():
    def __init__(self,n):
        self.pool = set(range(n))
        self.members = set()
        self.store = [None] * n
    def join(self,x):
        if len(self.pool) == 0: raise Exception('Pool full')
        i = self.pool.pop()
        x.id = i
        self.members.add(i)
        self.store[i] = x
    def pop(self):
        i = self.members.pop()
        self.pool.add(i)
        self.store[i] = None
    def leave(self,i):
        self.members.remove(i)
        self.pool.add(i)
        self.store[i] = None

class Arena():
    def __init__(self,n):
        self.bots = Pool(n)
        self.ammo = Pool(n)
        self.n = n
    def join(self,x):
        self.bots.join(x)
        self.ammo.join(Ammo())
    def leave(self,i):
        self.bots.leave(i)
        self.ammo.pop()

class Bot(basic.LineReceiver):
    delimiter = b'\n'
    def connectionMade(self):
        self.selfDestruct = reactor.callLater(60, self.leave)
        self.id = None
        self.name = ''
        self.ammo = 0
        self.rectimestamps = deque(maxlen=20)
        self.ip = self.transport.getPeer().host
        self.port = str(self.transport.getPeer().port)
        g_trace.debug(self.ip + ':' + self.port + ' |  | Connection made')
        self.message('PYBOT 2.0')

    def login(self):
        if not (self.selfDestruct.cancelled or self.selfDestruct.called): self.selfDestruct.cancel()
        self.selfDestruct = reactor.callLater(3600, self.leave)
        self.position = numpy.random.rand(2)
        self.direction = numpy.array([0.0, 1.0])
        self.speed = 0.0
        self.angle = 0.0
        self.battery = 1.0
        self.batterydrain = 4.0
        self.task = None
        self.timestamp = datetime.datetime.utcnow()
        try:
            self.factory.arena.join(self)
            self.ammo = 3
            self.message('WELCOME '+self.name+', YOUR ROBOT IS ' + colours[self.id].upper())
        except:
            self.message('ARENA FULL')
            self.transport.loseConnection()

    def currentState(self):
        t1 = datetime.datetime.utcnow()
        deltat = diff(self.timestamp,t1)
        position = self.position + deltat * self.speed * self.direction / 60.0
        self.timestamp = t1
        self.position = numpy.minimum(numpy.maximum(position, 0.0), 1.0)
        battery = self.battery + self.batterydrain * deltat / 60.0
        self.battery = numpy.minimum(numpy.maximum(battery, 0.0), 1.0)
        return self.position, self.battery

    def changeDirection(self,dir):
        self.currentState()
        self.angle = dir
        dir *= numpy.pi / 180
        self.direction = numpy.array([numpy.sin(dir),numpy.cos(dir)])
        self.currentState()

    def changeSpeed(self,speed):
        self.currentState()
        self.speed = min(abs(speed),5.0)
        self.batterydrain = -(0.25*self.speed*(self.speed+3)) if self.speed > 0 else 4.0
        self.currentState()
        self.checkBattery()

    def all(self):
        states = []
        pos0, _ = self.currentState()
        for i, client in enumerate(self.factory.arena.bots.store):
            if i == self.id or client is None: continue
            pos1, battery = client.currentState()
            diff = pos1 - pos0
            dist = numpy.sqrt(numpy.dot(diff, diff))
            direction = numpy.arctan2(diff[0], diff[1]) * 180 / numpy.pi
            if direction < 0.0: direction += 360
            states.append([round(dist,4), round(direction, 1), round(battery,4), client.ammo, client.speed, client.angle, client.id, colours[client.id].upper()])
        states.sort(key=lambda x: x[0])
        return states

    def allammo(self):
        states = []
        pos0, _ = self.currentState()
        for _ in self.factory.arena.ammo.store:
            if _ is None: continue
            diff = _.pos - pos0
            dist = numpy.sqrt(numpy.dot(diff, diff))
            direction = numpy.arctan2(diff[0], diff[1]) * 180 / numpy.pi
            if direction < 0.0: direction += 360
            states.append([round(dist, 4), round(direction, 1), _.id])
        states.sort(key = lambda x: x[0])
        return states

    def nearest(self):
        states = self.all()
        if len(states) == 0: return []
        return states[0]

    def checkBattery(self):
        if not self.task is None: self.task.cancel()
        if self.battery <= 0.0: self.die()
        elif self.batterydrain < 0.0: self.task = reactor.callLater(-self.battery / self.batterydrain * 60.0, self.die)
        else: self.task = None

    def leave(self):
        self.message('BYE')
        self.transport.loseConnection()

    def die(self):
        self.message('BATTERY DEAD')
        self.transport.loseConnection()

    def connectionLost(self, reason):
        if not (self.selfDestruct.called or self.selfDestruct.cancelled): self.selfDestruct.cancel()
        g_trace.debug(self.ip + ':' + self.port + ' | ' + self.name + ' | Connection lost')
        if not self.id is None:
            self.factory.arena.leave(self.id)

    def lineReceived(self, line):
        try:
            timestamp = datetime.datetime.utcnow()
            self.rectimestamps.append(timestamp)
            if len(self.rectimestamps) == self.rectimestamps.maxlen:
                if diff(self.rectimestamps[0], self.rectimestamps[-1]) < 1:
                    self.message('IGNORED')
                    return
            s = re.sub(r'[^a-zA-Z0-9 .,]', '', line.decode('ascii', errors='ignore')).strip()
            g_trace.debug(self.ip + ':' + self.port + ' | ' + self.name + ' | ' + s)
            received = s.upper().split(' ')
            command = received[0][:3]
            if self.id is None:
                if command == 'LOG':
                    if not len(received) == 2: return
                    self.name = received[1]
                    self.login()
                return
            if command == 'SPE':
                speed = float(received[1])
                self.changeSpeed(speed)
                self.message('OK')
            elif command == 'MOV':
                dir = float(received[1])
                if len(received) == 3: speed = float(received[2])
                else: speed = 1.0
                self.changeSpeed(speed)
                self.changeDirection(dir)
                self.message('OK')
            elif command == 'STO':
                self.changeSpeed(0.0)
                self.message('OK')
            elif command == 'POS':
                pos,battery = self.currentState()
                self.message(json.dumps([round(pos[0],4), round(pos[1],4)]))
            elif command == 'BAT':
                pos, battery = self.currentState()
                self.message(repr(round(battery,4)))
            elif command == 'STA':
                pos, battery = self.currentState()
                self.message(json.dumps([round(pos[0],4), round(pos[1],4), round(battery,4), self.ammo, self.speed, self.angle, self.id, colours[self.id].upper()]))
            elif command == 'NEA':
                state = self.nearest()
                self.message(json.dumps(state))
            elif command == 'ALL':
                state = self.all()
                self.message(json.dumps(state))
            elif command == 'AMM':
                state = self.allammo()
                self.message(json.dumps(state))
            elif command == 'PIC':
                state = self.allammo()
                found = False
                for _ in state:
                    if _[0] < 0.1:
                        id = _[2]
                        self.ammo += 1
                        self.factory.arena.ammo.leave(id)
                        self.factory.arena.ammo.join(Ammo())
                        found = True
                        break
                if found: self.message('OK')
                else: self.message('NONE')
            elif command == 'ZAP':
                pos0,_ = self.currentState()
                if self.ammo == 0: self. message('NO AMMO')
                else:
                    self.ammo -= 1
                    for client in self.factory.arena.bots.store:
                        if client == self or client is None: continue
                        pos1,_ = client.currentState()
                        dist = numpy.sqrt(numpy.dot(pos1-pos0,pos1-pos0))
                        if dist <= 0.05: client.battery -= 1.0
                        elif dist <= 0.1: client.battery -= (0.1 - dist) * 20
                        client.checkBattery()
                    self.message('OK')
            elif command == 'BYE':
                self.leave()
            else: raise Exception('Not understood')
        except Exception as e:
            g_trace.debug(self.ip + ':' + self.port + ' | ' + self.name + ' | error: ' + str(e))
            self.message('NOT UNDERSTOOD')

    def message(self, message):
        self.transport.write(message.encode('ascii') + b'\r\n')

factory = protocol.ServerFactory()
factory.protocol = Bot
factory.arena = Arena(len(colours))
endpoints.serverFromString(reactor, "tcp:8000").listen(Site(app.resource()))
reactor.listenTCP(8888,factory)
reactor.run()
