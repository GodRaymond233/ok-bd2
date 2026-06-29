# ok-bd2 发布配置指南

这份配置只需要做一次。配置完成后，推送 `v0.1.0` 这类 tag，GitHub
Actions 会自动生成 PyAppify 安装包，并同步 `ok-bd2-update` 更新仓库。

## 1. 创建 GitHub 更新仓库

在 GitHub 创建一个新仓库：

```text
GodRaymond233/ok-bd2-update
```

建议保持空仓库，不要勾选初始化 README、`.gitignore` 或 License。发布流程会
自动把 `deploy.txt` 中列出的文件同步进去。

## 2. 创建 CNB 更新仓库

在 CNB 创建一个新仓库：

```text
GodRaymond233/ok-bd2-update
```

`pyappify.yml` 里的 China 配置使用这个地址：

```text
https://cnb.cool/GodRaymond233/ok-bd2-update.git
```

CNB 支持直接使用浏览器仓库地址作为 Git 地址，也支持 `.git` 后缀格式。

## 3. 创建 GitHub token

在 GitHub 创建一个 Fine-grained personal access token，给它访问
`GodRaymond233/ok-bd2-update` 的权限。

推荐权限：

```text
Repository access: Only selected repositories -> GodRaymond233/ok-bd2-update
Contents: Read and write
Metadata: Read-only
```

生成后复制 token。这个 token 只会作为 GitHub Actions Secret 保存，不要提交到
仓库文件里。

## 4. 创建 CNB token

在 CNB 创建一个可以推送 `GodRaymond233/ok-bd2-update` 的访问令牌。它要能通过
HTTPS Git 写入仓库。

## 5. 添加 GitHub Actions Secrets

打开主仓库：

```text
https://github.com/GodRaymond233/ok-bd2
```

进入：

```text
Settings -> Secrets and variables -> Actions -> New repository secret
```

添加两个 Secret：

```text
OK_GH   = 第 3 步创建的 GitHub token
CNB_GH  = 第 4 步创建的 CNB token
```

不用添加 `GITHUB_TOKEN`，GitHub Actions 会自动提供。

## 6. 确认仓库配置

当前项目已经配置好这些文件：

```text
pyappify.yml
deploy.txt
.update_repo_gitignore
.github/workflows/build.yml
```

如果你以后改 GitHub 用户名或仓库名，需要同步修改：

```text
pyappify.yml
.github/workflows/build.yml
src/config.py
README.md
```

## 7. 发布

提交并推送主仓库后，创建一个正式版本 tag：

```powershell
git tag v0.1.0
git push origin v0.1.0
```

发布完成后，Release 页面应出现这些文件：

```text
ok-bd2-win32-China-setup.exe
ok-bd2-win32-online-setup.exe
ok-bd2-win32-Global-setup.exe
```

安装 `ok-bd2-win32-China-setup.exe` 后，本机目录会类似：

```text
C:\Users\<你>\AppData\Local\ok-bd2
```

双击 `ok-bd2.exe` 时，先出现白色 PyAppify 更新窗口；更新完成后再出现黑色
`ok-script` 主界面。
