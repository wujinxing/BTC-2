##################################
#       UNDER CONSTRUCTION       #
##################################

from autobahn.twisted.websocket import WebSocketClientFactory, connectWS
from twisted.python import log
from twisted.internet import reactor

import sys
import math
import scipy.stats

from blobprotocol import BlobProtocol
from volmonitor import VolMonitor
from book import Book, InsufficientSizeForVWAP
from strategy import RESTProtocol, Strategy, read_keys


class Helium(Strategy):
    def __init__(self, rest, params):
        Strategy.__init__(self, rest, debug=params['debug'])

        self.spread_factor    = params['spread_factor']
        self.trade_size       = params['trade_size']
        self.dump_on_lockdown = params['dump_on_lockdown']
        self.vol_thresh       = params['vol_thresh']
        self.max_distance     = params['max_distance']
        self.track_pnl        = params.get('track_pnl')
        self.stop_loss        = params.get('stop_loss')

        self.volmonitor = None
        self.previous_mid = None
        self.spread = None

    def compute_pnl(self):
        Strategy.compute_pnl(self)
        if self.track_pnl is not None:
            reactor.callLater(self.track_pnl, self.compute_pnl)

    # Main update loop.
    def update(self):
        if not self.enabled:
            return

        try:
            ask_vwap = self.book.get_vwap(self.trade_size)
            bid_vwap = self.book.get_vwap(-self.trade_size)
        except InsufficientSizeForVWAP as e:
            ask_vwap = self.book.get_best_ask()
            bid_vwap = self.book.get_best_bid()
        mid = 0.5*(ask_vwap + bid_vwap)

        # We want bids + position = trade_size...
        bid_size, ask_size = self.get_open_size()
        if bid_size + self.position < self.trade_size - 0.00000001:
            self.spread = self.spread_factor * 0.5 * (ask_vwap - bid_vwap) 
            price = mid - self.spread
            self.bid(self.trade_size - bid_size - self.position, price)

        # If outstanding asks are too far from mid, lockdown.
        if ask_size > 0.0:
            price = list(self.open_orders.values())[0].price
            if price - mid > self.max_distance:
                self.lockdown("max distance exceeded")

        # If stop-loss triggered, lockdown.
        if self.track_pnl is not None and self.profit_loss < -self.stop_loss:
            self.lockdown("stop loss of %0.2f triggered" % self.stop_loss)

        # We leave the vol monitor as optional, skip checks if not found.
        if self.volmonitor is None:
            return

        # Check for excessive volatility and lockdown if need be.
        vol = self.volmonitor.get_hourly_volatility()
        if vol >= self.vol_thresh:
            self.lockdown("excessive volatility")

    def lockdown(self, reason):
        Strategy.lockdown(self, reason)

    def on_place(self, oid, side, price, size, otype):
        Strategy.on_place(self, oid, side, price, size, otype)

    def on_place_fail(self, reason):
        Strategy.on_place_fail(self, reason)

    def on_partial_fill(self, order, remaining):
        Strategy.on_partial_fill(self, order, remaining)
        if order.side == "buy":
            price = order.price + self.spread
            self.ask(order.size - remaining, price)

    def on_complete_fill(self, order):
        Strategy.on_complete_fill(self, order)
        if order.side == "buy":
            price = order.price + self.spread
            self.ask(order.size, price)


if __name__ == '__main__':
    log.startLogging(sys.stdout)
    factory = WebSocketClientFactory('wss://ws-feed.exchange.coinbase.com')
    factory.protocol = BlobProtocol

    # Setup params from params.py.
    params_file = sys.argv[1]
    exec(compile(open(params_file).read(), params_file, 'exec')) 

    rest = RESTProtocol(read_keys('keys.txt'), debug=True)
    hh = Helium(rest, params=params)
    hh.enabled = False

    vm = VolMonitor(1.0)
    hh.volmonitor = vm

    bb = Book(factory.protocol, debug=False)
    bb.add_client(hh)
    bb.add_client(vm)

    connectWS(factory)

    if params.get("track_pnl") is not None:
        reactor.callLater(1.0, hh.compute_pnl)

    reactor.callLater(1.0, vm.generate_stamp)
    reactor.callLater(1.0, hh.enable)
    reactor.run()