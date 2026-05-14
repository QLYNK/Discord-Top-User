from threading import Thread

import keep_alive


if __name__ == "__main__":
    Thread(target=keep_alive.guild_cache_refresh_loop, daemon=True).start()
    keep_alive.run()
