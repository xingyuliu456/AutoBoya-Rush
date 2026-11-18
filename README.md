# AutoBoya Rush

北航博雅课的交互式定时选课工具。登录后刷新课程列表，输入一门课的序号即可按该课程的选课窗口开始尝试。以下步骤面向北京校区。

## 首次安装与配置

1. 安装 Conda。Windows 用户可以安装 Miniconda，装好后从开始菜单打开 `Anaconda Prompt`。 [Miniconda 安装文档](https://www.anaconda.com/docs/getting-started/miniconda/install)

2. 获取项目。装有 Git 时运行下面两条命令，也可以从 GitHub 下载 ZIP，解压后在终端进入包含 `requirements.txt` 的文件夹。

   ~~~shell
   git clone https://github.com/xingyuliu456/AutoBoya-Rush.git
   cd AutoBoya-Rush
   ~~~

3. 创建并激活 Python 3.12 环境，然后安装项目依赖。

   ~~~shell
   conda create -n autoboya-rush python=3.12 pip
   conda activate autoboya-rush
   python -m pip install -r requirements.txt
   ~~~

4. 把命令中的「你的学号」换成实际学号，登记统一认证账号。密码在提示出现后输入，终端输入时不会显示，AutoBoya 会将其保存到系统凭据库。这一步每个账号只需执行一次，已有凭据的账号可直接启动。

   ~~~shell
   python -m autoboya user add 你的学号
   ~~~

## 每次选课

在项目目录打开终端，激活环境并运行。

~~~shell
conda activate autoboya-rush
python autoboya_rush.py
~~~

1. 输入已登记的学号，程序随即登录。如果学校要求图形验证码，在终端输入图片上的验证码。Windows 会尝试打开图片，终端也会显示图片路径。
2. 登录成功后会先显示各类型的博雅要求与完成情况，统计口径与 `autoboya stats` 一致。随后列出尚未开放和正在开放的未选课程，包含课程类型，已满与窗口已结束的课程会被过滤。
3. 核对课程名称、上课时间和地点，然后输入列表里的序号。已开放的课会立即尝试，未开放的课会等到开放前约 2 秒再第一次尝试。
4. 选中后程序会刷新已选课程和统计。可在北航博雅网页核对，或运行以下命令查看已选记录。

   ~~~shell
   python -m autoboya selected --user 你的学号
   ~~~

如需直接传入学号，也可运行 `python autoboya_rush.py --user 你的学号`。

## 选课注意事项

- 建议在开放前几分钟启动，留出处理验证码的时间，并检查系统时钟与北京时间是否一致。
- 定时等待时，程序会在开放前约 25 秒校验会话。若要重登，留意终端的新验证码提示。
- 未到时间或遇到临时网络故障时，请求结束后等待约 0.4 秒再试。满员、选课上限、选课窗口结束或收到 `HTTP 429` 限流时会停止。请只开一个抢课进程。
- 选课前输入 0 可以退出。等待或尝试期间按 `Ctrl+C` 停止。程序会显示接口提供的退课截止时间，可提前核对。
- AutoBoya 的会话、验证码图片和课程缓存默认保存在个人目录的 `.autoboya` 文件夹。请保留在本机，避免把这些数据提交到 Git。

依赖的登录与课程接口由上游 AutoBoya 提供。 [AutoBoya 源码](https://github.com/DeNeRATe-cool/AutoBoya) · [PyPI](https://pypi.org/project/autoboya/0.1.5/)
