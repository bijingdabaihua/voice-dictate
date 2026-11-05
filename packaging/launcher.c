// Dictate 的 macOS 启动壳。
//
// 自包含 App 的布局：
//   Dictate.app/Contents/MacOS/Dictate          <- 本程序
//   Dictate.app/Contents/MacOS/DictatePython    <- 可移植 Python 解释器
//   Dictate.app/Contents/Resources/python/      <- Python 标准库
//   Dictate.app/Contents/Resources/site-packages/
//   Dictate.app/Contents/Resources/app/dictate.py
//
// 同时保留开发目录的兜底：如果 Resources/python 不存在，就在 App 的上一级
// 找 .venv/bin/python 和 dictate.py。这样从仓库直接 build 出来的 App 也能跑。
#include <errno.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

static int join_path(char *out, size_t out_size, const char *base, const char *suffix) {
    int written = snprintf(out, out_size, "%s%s", base, suffix);
    return written > 0 && (size_t)written < out_size ? 0 : -1;
}

static int find_bundle_root(char *out, size_t out_size) {
    char exe[PATH_MAX];
    uint32_t size = sizeof(exe);
    if (_NSGetExecutablePath(exe, &size) != 0) {
        return -1;
    }

    char resolved[PATH_MAX];
    if (realpath(exe, resolved) == NULL) {
        return -1;
    }

    // 去掉 Dictate / MacOS / Contents 三层，留下 .../Dictate.app
    for (int i = 0; i < 3; i++) {
        char *slash = strrchr(resolved, '/');
        if (slash == NULL || slash == resolved) {
            return -1;
        }
        *slash = '\0';
    }

    if (snprintf(out, out_size, "%s", resolved) >= (int)out_size) {
        return -1;
    }
    return 0;
}

static void show_error(void) {
    const char *message =
        "Dictate 安装不完整：找不到内置的 Python 或 dictate.py。\n"
        "请重新下载完整的 Dictate.app，或按 README 从源码构建。";
    fprintf(stderr, "%s\n", message);

    // 从 Finder 启动时没有终端，用系统弹窗告诉用户。
    pid_t pid = fork();
    if (pid == 0) {
        execlp(
            "osascript", "osascript", "-e",
            "display dialog \"Dictate 安装不完整，请重新下载完整的 App。\" "
            "buttons {\"好\"} default button 1 with icon caution",
            (char *)NULL
        );
        _exit(127);
    }
    if (pid > 0) {
        waitpid(pid, NULL, 0);
    }
}

int main(void) {
    setenv("HF_ENDPOINT", "https://hf-mirror.com", 0);
    setenv("HF_HUB_DISABLE_XET", "1", 0);
    setenv("PYTHONNOUSERSITE", "1", 1);
    setenv("PYTHONDONTWRITEBYTECODE", "1", 1);
    setenv("PYTHONUNBUFFERED", "1", 1);
    unsetenv("PYTHONHOME");

    char bundle[PATH_MAX];
    if (find_bundle_root(bundle, sizeof(bundle)) != 0) {
        show_error();
        return 1;
    }

    char resources[PATH_MAX];
    if (join_path(resources, sizeof(resources), bundle, "/Contents/Resources") != 0) {
        show_error();
        return 1;
    }

    char python[PATH_MAX];
    char app[PATH_MAX];
    char site_packages[PATH_MAX];
    int self_contained = 0;

    // Python 解释器放在 Contents/MacOS 下，NSBundle 才能把进程识别成
    // Dictate.app（权限弹窗显示 Dictate，而不是 python3）。
    if (join_path(python, sizeof(python), bundle, "/Contents/MacOS/DictatePython") == 0 &&
        join_path(app, sizeof(app), resources, "/app/dictate.py") == 0 &&
        join_path(site_packages, sizeof(site_packages), resources, "/site-packages") == 0 &&
        access(python, X_OK) == 0 && access(app, R_OK) == 0 &&
        access(site_packages, R_OK) == 0) {
        self_contained = 1;

        char python_home[PATH_MAX];
        if (join_path(python_home, sizeof(python_home), resources, "/python") != 0) {
            show_error();
            return 1;
        }
        setenv("PYTHONHOME", python_home, 1);

        char python_path[PATH_MAX * 2];
        snprintf(
            python_path, sizeof(python_path), "%s:%s/app",
            site_packages, resources
        );
        setenv("PYTHONPATH", python_path, 1);

        char hf_home[PATH_MAX];
        const char *home = getenv("HOME");
        if (home != NULL &&
            snprintf(
                hf_home, sizeof(hf_home),
                "%s/Library/Application Support/Dictate/huggingface", home
            ) < (int)sizeof(hf_home)) {
            setenv("HF_HOME", hf_home, 1);
        }
        if (home != NULL) {
            chdir(home);
        }
    } else {
        // 开发兜底：Dictate.app 在项目根目录下，上一级就是项目根。
        char root_raw[PATH_MAX];
        char root[PATH_MAX];
        if (join_path(root_raw, sizeof(root_raw), bundle, "/..") != 0 ||
            realpath(root_raw, root) == NULL) {
            show_error();
            return 1;
        }
        if (join_path(python, sizeof(python), root, "/.venv/bin/python") != 0 ||
            join_path(app, sizeof(app), root, "/dictate.py") != 0 ||
            access(python, X_OK) != 0 || access(app, R_OK) != 0) {
            show_error();
            return 1;
        }
        setenv("PYTHONPATH", root, 1);
        chdir(root);
    }

    pid_t pid = fork();
    if (pid == 0) {
        char *args[] = {python, app, NULL};
        execv(python, args);
        fprintf(stderr, "execv %s failed: %s\n", python, strerror(errno));
        _exit(127);
    }
    if (pid < 0) {
        show_error();
        return 1;
    }

    int status = 0;
    waitpid(pid, &status, 0);
    if (WIFEXITED(status)) {
        return WEXITSTATUS(status);
    }
    return 1;
}
