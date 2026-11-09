import argparse
from .server import Auth, ControlHTTPServer
from .storage import Store


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True); parser.add_argument("--bind", default="127.0.0.1"); parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--password", default=None); parser.add_argument("--password-env", default="MAA_CONTROL_PASSWORD")
    args = parser.parse_args(argv)
    import os
    password = args.password or os.environ.get(args.password_env)
    if not password: parser.error("provide --password or --password-env")
    server = ControlHTTPServer(Store(args.db), Auth(password), args.bind, args.port)
    server.serve_forever()


if __name__ == "__main__":
    main()
