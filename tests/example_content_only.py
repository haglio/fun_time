from fun_time import content

for _reader in (content.load_content, content.load_web_providers):
    _reader.__wrapped__.__defaults__ = (content.EXAMPLE_CONTENT, content.EXAMPLE_CONTENT)
