import asyncio
import socket
import sys
from argparse import ArgumentParser
from collections import Iterable
from importlib import import_module

from yarl import URL

from . import (web_exceptions, web_fileresponse, web_middlewares, web_protocol,
               web_request, web_response, web_server, web_site,
               web_urldispatcher, web_ws)
from .http import HttpVersion  # noqa
from .log import access_logger
from .web_application import Application  # noqa
from .web_exceptions import *  # noqa
from .web_fileresponse import *  # noqa
from .web_middlewares import *  # noqa
from .web_protocol import *  # noqa
from .web_request import *  # noqa
from .web_response import *  # noqa
from .web_server import *  # noqa
from .web_site import AppRunner, GracefulExit, SockSite, TCPSite, UnixSite
from .web_urldispatcher import *  # noqa
from .web_ws import *  # noqa


__all__ = (web_protocol.__all__ +
           web_fileresponse.__all__ +
           web_request.__all__ +
           web_response.__all__ +
           web_exceptions.__all__ +
           web_urldispatcher.__all__ +
           web_ws.__all__ +
           web_server.__all__ +
           web_site.__all__ +
           web_middlewares.__all__ +
           ('Application', 'HttpVersion', 'MsgType'))


def _make_server_creators(handler, *, loop, ssl_context,
                          host, port, path, sock, backlog):

    scheme = 'https' if ssl_context else 'http'
    base_url = URL.build(scheme=scheme, host='localhost', port=port)

    if path is None:
        paths = ()
    elif isinstance(path, (str, bytes, bytearray, memoryview))\
            or not isinstance(path, Iterable):
        paths = (path,)
    else:
        paths = path

    if sock is None:
        socks = ()
    elif not isinstance(sock, Iterable):
        socks = (sock,)
    else:
        socks = sock

    if host is None:
        if (paths or socks) and not port:
            hosts = ()
        else:
            hosts = ("0.0.0.0",)
    elif isinstance(host, (str, bytes, bytearray, memoryview))\
            or not isinstance(host, Iterable):
        hosts = (host,)
    else:
        hosts = host

    if hosts and port is None:
        port = 8443 if ssl_context else 8080

    server_creations = []
    uris = [str(base_url.with_host(host).with_port(port)) for host in hosts]
    if hosts:
        # Multiple hosts bound to same server is available in most loop
        # implementations, but only send multiple if we have multiple.
        host_binding = hosts[0] if len(hosts) == 1 else hosts
        server_creations.append(
            loop.create_server(
                handler, host_binding, port, ssl=ssl_context, backlog=backlog
            )
        )
    for path in paths:
        # Most loop implementations don't support multiple paths bound in same
        # server, so create a server for each.
        server_creations.append(
            loop.create_unix_server(
                handler, path, ssl=ssl_context, backlog=backlog
            )
        )
        uris.append('{}://unix:{}:'.format(scheme, path))

    for sock in socks:
        server_creations.append(
            loop.create_server(
                handler, sock=sock, ssl=ssl_context, backlog=backlog
            )
        )

        if hasattr(socket, 'AF_UNIX') and sock.family == socket.AF_UNIX:
            uris.append('{}://unix:{}:'.format(scheme, sock.getsockname()))
        else:
            host, port = sock.getsockname()[:2]
            uris.append(str(base_url.with_host(host).with_port(port)))
    return server_creations, uris


def run_app(app, *, host=None, port=None, path=None, sock=None,
            shutdown_timeout=60.0, ssl_context=None,
            print=print, backlog=128, access_log_format=None,
            access_log=access_logger, handle_signals=True):
    """Run an app locally"""
    loop = asyncio.get_event_loop()

    runner = AppRunner(app, handle_signals=handle_signals,
                       access_log_format=access_log_format,
                       access_log=access_log)

    loop.run_until_complete(runner.setup())

    sites = []

    try:
        if host is None and port is None and sock is None:
            sites.append(TCPSite(runner, shutdown_timeout=shutdown_timeout,
                                 ssl_context=ssl_context, backlog=backlog))
        else:
            if host is not None:
                if isinstance(host, (str, bytes, bytearray, memoryview)):
                    sites.append(TCPSite(runner, host, port,
                                         shutdown_timeout=shutdown_timeout,
                                         ssl_context=ssl_context,
                                         backlog=backlog))
                else:
                    for h in host:
                        sites.append(TCPSite(runner, h, port,
                                             shutdown_timeout=shutdown_timeout,
                                             ssl_context=ssl_context,
                                             backlog=backlog))
            elif (host is None and port is not None and
                      (path is None or sock is None)):
                sites.append(TCPSite(runner, port=port,
                                     shutdown_timeout=shutdown_timeout,
                                     ssl_context=ssl_context, backlog=backlog))

            if path is not None:
                if isinstance(path, (str, bytes, bytearray, memoryview)):
                    sites.append(UnixSite(runner, path,
                                          shutdown_timeout=shutdown_timeout,
                                          ssl_context=ssl_context,
                                          backlog=backlog))
                else:
                    for p in path:
                        sites.append(UnixSite(runner, p,
                                              shutdown_timeout=shutdown_timeout,
                                              ssl_context=ssl_context,
                                              backlog=backlog))

            if sock is not None:
                if not isinstance(sock, Iterable):
                    sites.append(SockSite(runner, sock,
                                          shutdown_timeout=shutdown_timeout,
                                          ssl_context=ssl_context,
                                          backlog=backlog))
                else:
                    for s in sock:
                        sites.append(SockSite(runner, s,
                                              shutdown_timeout=shutdown_timeout,
                                              ssl_context=ssl_context,
                                              backlog=backlog))
        for site in sites:
            loop.run_until_complete(site.start())
        try:
            if print:
                urls = sorted(str(s.url) for s in runner.sites)
                print("======== Running on {} ========\n"
                      "(Press CTRL+C to quit)".format(', '.join(urls)))
            loop.run_forever()
        except (GracefulExit, KeyboardInterrupt):  # pragma: no cover
            pass
    finally:
        loop.run_until_complete(runner.cleanup())
    if hasattr(loop, 'shutdown_asyncgens'):
        loop.run_until_complete(loop.shutdown_asyncgens())
    loop.close()


def main(argv):
    arg_parser = ArgumentParser(
        description="aiohttp.web Application server",
        prog="aiohttp.web"
    )
    arg_parser.add_argument(
        "entry_func",
        help=("Callable returning the `aiohttp.web.Application` instance to "
              "run. Should be specified in the 'module:function' syntax."),
        metavar="entry-func"
    )
    arg_parser.add_argument(
        "-H", "--hostname",
        help="TCP/IP hostname to serve on (default: %(default)r)",
        default="localhost"
    )
    arg_parser.add_argument(
        "-P", "--port",
        help="TCP/IP port to serve on (default: %(default)r)",
        type=int,
        default="8080"
    )
    arg_parser.add_argument(
        "-U", "--path",
        help="Unix file system path to serve on. Specifying a path will cause "
             "hostname and port arguments to be ignored.",
    )
    args, extra_argv = arg_parser.parse_known_args(argv)

    # Import logic
    mod_str, _, func_str = args.entry_func.partition(":")
    if not func_str or not mod_str:
        arg_parser.error(
            "'entry-func' not in 'module:function' syntax"
        )
    if mod_str.startswith("."):
        arg_parser.error("relative module names not supported")
    try:
        module = import_module(mod_str)
    except ImportError as ex:
        arg_parser.error("unable to import %s: %s" % (mod_str, ex))
    try:
        func = getattr(module, func_str)
    except AttributeError:
        arg_parser.error("module %r has no attribute %r" % (mod_str, func_str))

    # Compatibility logic
    if args.path is not None and not hasattr(socket, 'AF_UNIX'):
        arg_parser.error("file system paths not supported by your operating"
                         " environment")

    app = func(extra_argv)
    run_app(app, host=args.hostname, port=args.port, path=args.path)
    arg_parser.exit(message="Stopped\n")


if __name__ == "__main__":  # pragma: no branch
    main(sys.argv[1:])  # pragma: no cover
