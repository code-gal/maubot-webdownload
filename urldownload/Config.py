from typing import Type
from mautrix.util.config import BaseProxyConfig, ConfigUpdateHelper
from maubot import Plugin, MessageEvent
from maubot.handlers import command


class Config(BaseProxyConfig):
    def do_update(self, helper: ConfigUpdateHelper) -> None:
        helper.copy("whitelist")
        helper.copy("command_prefix")
        helper.copy("url_regex")
        helper.copy("mimetype_regex")
        helper.copy("extension_regex")
