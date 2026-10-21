/* Leadership overview uses only persisted business statistics. */
function initializeExecutiveDashboard() {
    // Clear legacy demonstration content before requesting real statistics.
    document.querySelectorAll('.metric-value').forEach(el => { el.textContent = '—'; });
    document.querySelectorAll('.metric-note, .metric-change').forEach(el => { el.textContent = '等待数据'; });
    ['.ranking-list', '.qa-list', '.status-list'].forEach(selector => {
        document.querySelector(selector).innerHTML = '<div class="dashboard-empty">等待业务数据</div>';
    });
    ['uploadAreaPath', 'questionAreaPath', 'uploadLinePath', 'questionLinePath'].forEach(id => document.getElementById(id).setAttribute('d', ''));
    document.getElementById('presentationToggle').addEventListener('click', function () {
        const active = document.body.classList.toggle('dashboard-presentation');
        this.setAttribute('aria-pressed', String(active));
        this.textContent = active ? '退出演示' : '演示模式';
    });
    document.addEventListener('keydown', event => {
        if (event.key === 'Escape' && document.body.classList.contains('dashboard-presentation')) document.getElementById('presentationToggle').click();
    });
    window.setInterval(() => {
        if (document.getElementById('autoRefresh').checked && !document.hidden && getStoredDashboardUser()) loadDashboardData(false);
    }, 60000);
}

function renderExecutiveOverview(data) {
    const metrics = data.metrics || {};
    const docs = metrics.documents || {};
    const overview = data.overview || {};
    const count = value => Math.max(0, Number(value) || 0);
    const knowledge = count(docs.knowledge), breakdown = count(docs.breakdown);
    const published = knowledge + breakdown;
    const pending = count(docs.pending_review);
    const failed = count((data.document_categories || []).find(row => row.key === 'parse_failed')?.count);
    const unindexed = count(overview.unindexed_documents);
    const answers = count(overview.today_ai_answers);
    const referenced = count(overview.today_referenced_answers);
    const coverage = answers ? Math.min(100, referenced / answers * 100) : null;
    const enabled = count(overview.enabled_users);
    const penetration = enabled ? (count(overview.week_active_users) / enabled * 100).toFixed(1) : null;
    const percent = published ? knowledge / published * 100 : 0;
    document.getElementById('overviewState').textContent = '业务数据已更新';
    document.getElementById('executiveSummary').textContent = `已沉淀 ${formatNumber(published)} 篇知识与故障文档，近七日服务 ${formatNumber(overview.week_active_users)} 位用户、处理 ${formatNumber(overview.week_questions)} 次提问。${pending || failed || unindexed ? `重点关注：${pending} 篇待审核、${failed} 份解析失败、${unindexed} 篇待索引。` : '当前无待审核、解析失败或待索引记录。'}`;
    const stat = (label, value, unit, note) => `<div class="overview-stat"><span>${label}</span><strong>${value}<small>${unit}</small></strong><p>${note}</p></div>`;
    document.getElementById('executiveOverview').innerHTML = `
        <article class="dashboard-panel overview-panel"><div class="overview-heading"><span>01 / 使用成效</span><i class="fas fa-chart-line"></i></div>
        ${stat('近七日累计提问', formatNumber(overview.week_questions), '次', '近七个自然日（含今日）')}
        <div class="overview-pair"><div><b>${formatNumber(overview.week_active_users)}</b><span>七日去重活跃用户</span></div><div><b>${penetration === null ? '—' : penetration + '%'}</b><span>活跃人数 / 启用账号</span></div></div>
        <p class="overview-footnote">活跃包含登录、问答、上传及审核；活跃口径可能包含已停用账号。</p></article>
        <article class="dashboard-panel overview-panel"><div class="overview-heading"><span>02 / 知识资产</span><i class="fas fa-layer-group"></i></div>
        ${stat('正式文档存量', formatNumber(published), '篇', '知识库与故障库文档，不含待审核')}
        <div class="asset-track" role="img" aria-label="知识库 ${knowledge} 篇，故障库 ${breakdown} 篇"><span style="width:${percent}%"></span></div>
        <div class="asset-legend"><span>● 知识库 <b>${formatNumber(knowledge)}</b></span><span>● 故障库 <b>${formatNumber(breakdown)}</b></span></div>
        <p class="overview-footnote">待索引 ${formatNumber(unindexed)} 篇；索引完成后才能更好支持检索。</p></article>
        <article class="dashboard-panel overview-panel"><div class="overview-heading"><span>03 / 知识支撑</span><i class="fas fa-comments"></i></div>
        ${stat('今日回答引用覆盖率', coverage === null ? '—' : coverage.toFixed(1), coverage === null ? '' : '%', answers ? `${formatNumber(referenced)} 条回答记录带有文档引用 / 共 ${formatNumber(answers)} 条回答` : '今日暂无 AI 回答记录')}
        <div class="coverage-track"><span style="width:${coverage || 0}%"></span></div><p class="overview-footnote">反映回答的知识引用情况，不代表答案正确率或维修解决率。</p></article>
        <article class="dashboard-panel overview-panel attention-panel"><div class="overview-heading"><span>04 / 重点关注</span><i class="fas fa-flag"></i></div>
        <div class="attention-row"><span>待审核文档</span><b class="${pending ? 'attention-value' : ''}">${formatNumber(pending)} <small>篇</small></b></div>
        <div class="attention-row"><span>解析失败文件</span><b class="${failed ? 'attention-value' : ''}">${formatNumber(failed)} <small>份</small></b></div>
        <div class="attention-row"><span>待索引文档</span><b class="${unindexed ? 'attention-value' : ''}">${formatNumber(unindexed)} <small>篇</small></b></div>
        <p class="overview-footnote">${pending || failed || unindexed ? '建议优先推进审核、排查解析失败并完成知识索引。各项可能重叠，不累计为任务总数。' : '暂无上述待办，持续关注知识更新与服务使用情况。'}</p></article>`;
    // Make the existing metric's statistical meaning explicit.
    const note = document.querySelectorAll('.metric-card')[3]?.querySelector('.metric-note');
    if (note) note.title = '回答/提问比与引用覆盖率均不代表答案正确率';
}
