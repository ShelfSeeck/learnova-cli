"""扁平异常树。code 是稳定机器可读标识，属于输出契约。

错误只向上抛，由 Router 单点捕获；命令与引擎不写 try/except。
"""


class CliError(Exception):
    def __init__(self, code: str, message: str, exit_code: int = 1):
        super().__init__(message)
        self.code = code
        self.message = message
        self.exit_code = exit_code


class ConflictError(CliError):
    def __init__(self, code: str, message: str):
        super().__init__(code, message, exit_code=3)


class AuthError(CliError):
    def __init__(self, code: str, message: str):
        super().__init__(code, message, exit_code=4)
