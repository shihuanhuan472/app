(function () {
    const $ = id => document.getElementById(id);
    const form = $('feedbackForm'), files = $('files'), list = $('feedbackList');
    const client = userAPI.client;
    let page = 1;
    const escapeHtml = value => String(value || '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    async function request(path, options = {}) {
        const headers = {Authorization: `Bearer ${localStorage.getItem('token') || sessionStorage.getItem('token') || ''}`};
        let response = await fetch(`${API_CONFIG.BASE_URL}${path}`, {...options, headers});
        if (response.status === 401) {
            headers.Authorization = `Bearer ${await client.refreshToken()}`;
            response = await fetch(`${API_CONFIG.BASE_URL}${path}`, {...options, headers});
        }
        if (!response.ok) {
            const error = await response.json().catch(() => ({}));
            throw Error(client._extractErrorMessage(error) || '请求失败');
        }
        return response;
    }
    const title = document.querySelector('.feedback-history h2');
    const formDescription = document.createElement('p');
    formDescription.className = 'panel-description';
    formDescription.textContent = '每一条反馈，都在帮助我们把系统做得更好。';
    form.querySelector('h2').after(formDescription);
    const scopeLabel = document.createElement('p');
    title.after(scopeLabel);
    const pagination = document.createElement('div');
    pagination.className = 'feedback-pagination';
    list.after(pagination);
    const isAdmin = Utils.hasPermission(Utils.getCurrentUser(), Utils.PERMISSION.ADMIN);
    title.textContent = isAdmin ? '全部用户反馈' : '我的反馈';
    $('details').oninput = e => $('detailCount').textContent = e.target.value.length;
    files.onchange = () => $('fileNames').textContent = Array.from(files.files).map(f => f.name).join('\n') || '未选择文件';
    client.get('/user/profile').then(r => {
        if (r.code === 1 && r.data) $('person').value = r.data.full_name || r.data.username || '';
    }).catch(() => {});
    form.onsubmit = async e => {
        e.preventDefault();
        const selected = Array.from(files.files);
        if (selected.length > 6) return alert('图片和附件最多 6 个');
        if (selected.some(f => f.size > 10 * 1024 * 1024)) return alert('单个附件不能超过 10 MB');
        const data = new FormData();
        [['software','software'],['feedbackType','feedback_type'],['description','description'],['details','details'],['department','department'],['person','person']].forEach(([id,key]) => data.append(key, $(id).value));
        selected.forEach(f => data.append('files', f));
        const button = form.querySelector('button');
        button.disabled = true;
        button.innerHTML = '<i class="fas fa-spinner fa-spin"></i> 正在提交…';
        $('formMessage').removeAttribute('data-error');
        try {
            const result = await (await request('/user-feedback', {method:'POST', body:data})).json();
            if (result.code !== 1) throw Error(result.msg || '提交失败');
            $('formMessage').textContent = '反馈已提交，谢谢你的建议！';
            const person = $('person').value;
            form.reset();
            $('person').value = person;
            $('detailCount').textContent = '0';
            $('fileNames').textContent = '未选择文件';
            page = 1;
            await load();
        } catch (error) { $('formMessage').textContent = error.message; $('formMessage').dataset.error = 'true'; }
        finally { button.disabled = false; button.innerHTML = '<i class="fas fa-paper-plane"></i> 提交反馈'; }
    };
    list.onclick = async e => {
        const link = e.target.closest('[data-download]');
        if (!link) return;
        e.preventDefault();
        try {
            const response = await request(link.dataset.download);
            const url = URL.createObjectURL(await response.blob());
            const a = document.createElement('a');
            a.href = url;
            a.download = link.dataset.name;
            a.click();
            setTimeout(() => URL.revokeObjectURL(url), 1000);
        } catch (error) { alert(error.message); }
    };
    async function load() {
        try {
            const result = await client.get(`/user-feedback?page=${page}&page_size=20`);
            if (result.code !== 1 || !result.data) throw Error(result.msg || '加载失败');
            const {items, total, scope} = result.data;
            const all = scope ? scope === 'all' : isAdmin;
            title.textContent = all ? '全部用户反馈' : '我的反馈';
            scopeLabel.textContent = `${all ? '管理员可查看所有用户提交的反馈' : '仅展示你提交的反馈'}，共 ${total} 条`;
            list.innerHTML = items.length ? items.map(item => `<article class="feedback-item"><div class="feedback-item-head"><span class="feedback-type ${item.feedback_type === '需求' ? 'is-request' : ''}"><i class="fas ${item.feedback_type === '需求' ? 'fa-lightbulb' : 'fa-exclamation-circle'}"></i> ${escapeHtml(item.feedback_type)}</span><span class="feedback-software">${escapeHtml(item.software)}</span><span>#${item.id}</span></div><p class="feedback-description">${escapeHtml(item.description)}</p>${item.details ? `<details><summary>查看详细描述</summary><p style="white-space:pre-wrap">${escapeHtml(item.details)}</p></details>` : ''}${(item.attachments || []).map(a => `<div><a class="feedback-attachment" href="#" data-download="/user-feedback/${item.id}/attachments/${a.index}" data-name="${escapeHtml(a.name)}"><i class="fas fa-paperclip"></i>&nbsp; ${escapeHtml(a.name)}</a></div>`).join('')}<div class="feedback-meta"><span class="feedback-author"><i class="far fa-user"></i> ${escapeHtml(item.person)}</span><span>${escapeHtml(item.department)}</span>${all ? `<span>用户 ${item.user_id}</span>` : ''}<time class="feedback-time">${new Date(item.created_time).toLocaleString('zh-CN', {month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'})}</time></div></article>`).join('') : '<div class="empty"><i class="far fa-comment-dots"></i>还没有反馈记录<br>你的想法，值得被听见</div>';
            const pages = Math.max(1, Math.ceil(total / 20));
            pagination.innerHTML = `<button type="button" class="btn btn-secondary btn-sm" ${page <= 1 ? 'disabled' : ''}>上一页</button> <span>${page} / ${pages}</span> <button type="button" class="btn btn-secondary btn-sm" ${page >= pages ? 'disabled' : ''}>下一页</button>`;
            const buttons = pagination.querySelectorAll('button');
            buttons[0].onclick = () => { page--; load(); };
            buttons[1].onclick = () => { page++; load(); };
        } catch (error) { list.textContent = `反馈记录加载失败：${error.message}`; }
    }
    load();
})();
