import time

import pg8000.native


connections = []
while True:
    healthy_connections = []
    for connection in connections:
        try:
            connection.run("SELECT 1")
            healthy_connections.append(connection)
        except Exception as error:
            print(f"held connection was lost ({error})", flush=True)
            try:
                connection.close()
            except Exception:
                pass
    connections = healthy_connections

    while len(connections) < 12:
        try:
            connections.append(
                pg8000.native.Connection(
                    user="postgres",
                    password="password",
                    host="db",
                    database="appdb",
                    timeout=3,
                    application_name="nightly-batch",
                )
            )
            print(f"holding {len(connections)} connections", flush=True)
        except Exception as error:
            print(f"connect failed ({error}), retrying", flush=True)
            break

    time.sleep(1)
