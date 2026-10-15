(() => {
    const field = id => document.getElementById(id);
    const message = (text, type = 'info') => {
        field('message').textContent = text;
        field('message').classList.toggle('is-error', type === 'error');
    };
    if (new URLSearchParams(location.search).get('from') === 'mobile') {
        field('backToLogin').href = 'mobile/login.html';
    }
    const identity = () => ({username: field('username').value.trim(), phone: field('phone').value.trim()});
    async function request(path, payload) {
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), 30000);
        try {
            const response = await fetch(`${API_CONFIG.BASE_URL}/auth/password-reset/${path}`, {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(payload), signal: controller.signal
            });
            const result = await response.json();
            if (!response.ok || result.code !== 1) {
                throw new Error(result.msg || '请求失败，请检查填写的信息或稍后重试');
            }
            return result;
        } finally { clearTimeout(timer); }
    }
    field('sendCode').addEventListener('click', async () => {
        if (!field('username').reportValidity() || !field('phone').reportValidity()) return;
        const button = field('sendCode');
        button.disabled = true;
        let sent = false;
        try {
            message('正在发送，请稍候…');
            const result = await request('code', identity());
            message(result.msg);
            sent = true;
            let seconds = 60;
            button.textContent = `${seconds}秒后可重新发送`;
            const timer = setInterval(() => {
                seconds -= 1;
                button.textContent = `${seconds}秒后可重新发送`;
                if (seconds <= 0) {
                    clearInterval(timer); button.disabled = false;
                    button.textContent = '发送钉钉验证码';
                }
            }, 1000);
        } catch (error) { message(error.name === 'AbortError' ? '请求超时，请稍后重试' : error.message, 'error'); }
        finally { if (!sent) button.disabled = false; }
    });
    field('resetForm').addEventListener('submit', async event => {
        event.preventDefault();
        if (field('newPassword').value !== field('confirmPassword').value) {
            message('两次输入的密码不一致', 'error'); return;
        }
        const button = field('submitReset');
        button.disabled = true;
        try {
            await request('confirm', {...identity(), code: field('code').value,
                new_password: field('newPassword').value, confirm_password: field('confirmPassword').value});
            field('resetForm').reset();
            window.location.replace(field('backToLogin').href);
        } catch (error) { message(error.name === 'AbortError' ? '请求超时，请稍后重试' : error.message, 'error'); }
        finally { button.disabled = false; }
    });
})();
