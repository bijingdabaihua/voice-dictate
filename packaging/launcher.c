// 极简 macOS 启动壳：fork 出 venv 里的 python 跑 dictate.py。
// 用原生 Mach-O 作为 App 主程序，LaunchServices / TCC 才认这个 bundle。
#include <stdio.h>
#include <stdlib.h>
#include <sys/wait.h>
#include <unistd.h>

#ifndef ROOT
#define ROOT "/Users/dr/Documents/Project/voice-dictate"
#endif

int main(void) {
    setenv("HF_ENDPOINT", "https://hf-mirror.com", 0);
    setenv("HF_HUB_DISABLE_XET", "1", 0);
    chdir(ROOT);

    char python[4096];
    char script[4096];
    snprintf(python, sizeof(python), "%s/.venv/bin/python", ROOT);
    snprintf(script, sizeof(script), "%s/dictate.py", ROOT);

    pid_t pid = fork();
    if (pid == 0) {
        char *args[] = {python, script, NULL};
        execv(python, args);
        _exit(127);
    }

    int status = 0;
    waitpid(pid, &status, 0);
    return WIFEXITED(status) ? WEXITSTATUS(status) : 1;
}
