/* Shares the existing import page's source list and parse/review workflow. */
async function monitorConfluenceTask(task, client) {
    const endpoint = API_CONFIG.ENDPOINTS.DOCUMENTS + '/parse_tasks';
    while (!['finished', 'failed'].includes(task.status)) {
        document.getElementById('confluenceStatus').textContent =
            `后台解析任务 ${task.id}：${task.success_count + task.failed_count}/${task.total_count}，${task.current_file_name || '等待中'}`;
        await new Promise(resolve => setTimeout(resolve, 2000));
        const response = await client.get(endpoint + '/' + task.id);
        if (response.code !== 1) throw new Error(response.msg || '任务查询失败，后台任务仍可能继续执行');
        task = response.data;
    }
    localStorage.removeItem('confluenceParseTask');
    return {parse_results: task.items, success_origin_filename: task.items.filter(i => i.status === 'success').map(i => i.file_name),
        error_origin_filename: task.items.filter(i => i.status !== 'success').map(i => i.file_name)};
}

window.analyzeConfluenceFiles = async function (files, names, review, library, tags) {
    if (library !== 'knowledge') throw new Error('Confluence 文档请选择知识库');
    const client = new APIClient();
    const endpoint = API_CONFIG.ENDPOINTS.DOCUMENTS + '/parse_tasks';
    const created = await client.post(endpoint, {file_list: files, file_name: names,
        submit_for_review: review, library_type: library, tag: tags});
    if (created.code !== 1) throw new Error(created.msg || '创建解析任务失败');
    const task = created.data;
    localStorage.setItem('confluenceParseTask', String(task.id));
    return monitorConfluenceTask(task, client);
};

document.addEventListener('DOMContentLoaded', () => {
    // Initialize only when the import panel is enabled.
    if (document.getElementById('confluenceImport')?.dataset.enabled !== 'true') return;
    const stage = document.getElementById('confluenceStage');
    const status = document.getElementById('confluenceStatus');
    const root = document.getElementById('confluenceRoot');
    const client = new APIClient();
    let busy = false;
    let activityTimer = null;
    let activityStarted = 0;
    async function post(path, body) {
        const response = await client.post('/confluence/' + path, body);
        if (response.code !== 1) throw new Error(response.msg || '请求失败');
        return response.data;
    }
    function setBusy(value) {
        busy = value;
        clearInterval(activityTimer);
        activityTimer = null;
        const activity = document.getElementById('confluenceActivity');
        if (activity) activity.hidden = !value;
        if (value) {
            status.dataset.state = 'loading';
            activityStarted = Date.now();
            const updateElapsed = () => {
                const seconds = Math.floor((Date.now() - activityStarted) / 1000);
                const elapsed = document.getElementById('confluenceElapsed');
                if (elapsed) elapsed.textContent = `已等待 ${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`;
            };
            updateElapsed();
            activityTimer = setInterval(updateElapsed, 1000);
        }
        root.disabled = value || isParsingFiles;
        stage.disabled = value || isParsingFiles;
        document.getElementById('confluenceAttachments').disabled = value || isParsingFiles;
        stage.setAttribute('aria-busy', String(value));
        stage.innerHTML = value
            ? '<i class="fas fa-spinner fa-spin" aria-hidden="true"></i> 导入中…'
            : '<i class="fas fa-cloud-arrow-down" aria-hidden="true"></i> 导入整个分支';
    }
    const previousTask = localStorage.getItem('confluenceParseTask');
    if (previousTask && /^\d+$/.test(previousTask)) {
        const resume = document.createElement('button');
        resume.type = 'button';
        resume.className = 'btn btn-secondary';
        resume.textContent = '查看上次解析任务';
        status.after(resume);
        resume.addEventListener('click', async () => {
            if (isParsingFiles) return;
            resume.disabled = true;
            setParsingControlsLocked(true);
            try {
                const response = await client.get(API_CONFIG.ENDPOINTS.DOCUMENTS + '/parse_tasks/' + previousTask);
                if (response.code !== 1) throw new Error(response.msg || '查询失败');
                showAnalyzeStep();
                document.getElementById('analyzeBtn').disabled = true;
                const result = await monitorConfluenceTask(response.data, client);
                renderAnalyzeResult(result);
                status.textContent = '上次任务已结束，结果见下方。';
                resume.remove();
            } catch (error) {
                status.textContent = error.message;
                resume.disabled = false;
                document.getElementById('analyzeBtn').disabled = uploadedFiles.length === 0;
            } finally {
                setParsingControlsLocked(false);
            }
        });
    }
    root.addEventListener('input', () => {
        status.textContent = '';
    });
    let activeJob = null;
    let jobPollTimer = null;
    const resumeJob = document.createElement('button');
    resumeJob.type = 'button';
    resumeJob.id = 'confluenceResumeJob';
    resumeJob.className = 'btn btn-secondary';
    resumeJob.textContent = '继续采集';
    resumeJob.hidden = true;
    status.after(resumeJob);

    function displayJob(job) {
        activeJob = job;
        root.value = job.root_url;
        document.getElementById('confluenceAttachments').checked = job.include_attachments;
        if (job.status === 'running') {
            if (!busy) setBusy(true);
            activityStarted = job.started_at * 1000;
        } else {
            setBusy(false);
        }
        resumeJob.hidden = job.status !== 'paused';
        stage.disabled = busy || isParsingFiles || job.status === 'paused';
        const p = job.progress || {};
        const current = document.getElementById('confluenceCurrent');
        if (current) current.textContent = `第 ${p.batch || job.batches + 1} 批 · 每批 100 页 · 已处理 ${job.processed} 页 · 待处理 ${job.pending_count} 页\n`
            + (p.phase || '准备采集') + ' ' + (p.title || '')
            + (p.page_id ? `（页面 ID：${p.page_id}）` : '')
            + (p.url ? `\n当前请求：${p.url}` : '');
        const added = [];
        for (const file of job.files || []) {
            if (['uploaded', 'parse_failed'].includes(file.status) && !uploadedFiles.some(f => f.url === file.url)) {
                uploadedFiles.push({...file, sourceId: file.id});
                added.push(file);
            }
        }
        if (added.length) {
            renderUploadedFilesList();
            const library = document.getElementById('libraryType');
            library.value = 'knowledge';
            library.dispatchEvent(new Event('change'));
        }
        if (job.status === 'finished' && uploadedFiles.length) showAnalyzeStep();
        // Do not parse a partially collected branch while later batches can refresh its sources.
        const analyze = document.getElementById('analyzeBtn');
        if (analyze) analyze.disabled = busy || isParsingFiles || uploadedFiles.length === 0;
        const label = job.status === 'finished' ? '采集完成，请确认文件后开始解析。'
            : job.status === 'paused' ? '采集已暂停，已完成批次保留。处理问题后点击“继续采集”。'
            : '正在自动分批采集，刷新页面后会恢复任务进度。';
        status.dataset.state = job.status === 'paused' ? 'error' : 'normal';
        status.textContent = `${label}\n已完成 ${job.batches} 批，处理 ${job.processed} 页，入库文件 ${job.files.length} 个，跳过 ${job.skipped_files} 个。`
            + (job.status === 'paused' ? `\n${job.error}` : '')
            + (job.warnings.length ? '\n注意：' + job.warnings.join('\n注意：') : '');
        const notices = document.getElementById('confluenceNotices');
        if (notices) notices.textContent = [...job.notices,
            ...job.failures.map(f => `页面 ${f.title}（${f.page_id}）未更新，旧数据保留：${f.error}`)].join('\n');
    }

    async function followJob(token) {
        clearTimeout(jobPollTimer);
        try {
            const response = await client.get('/confluence/import-jobs/' + token);
            if (response.code !== 1) throw new Error(response.msg || '任务查询失败');
            displayJob(response.data);
            if (response.data.status !== 'running') return;
        } catch (error) {
            status.textContent = '暂时无法查询采集进度，正在重试；请勿重复提交。';
        }
        jobPollTimer = setTimeout(() => followJob(token), 2000);
    }

    resumeJob.addEventListener('click', async () => {
        if (!activeJob) return;
        resumeJob.disabled = true;
        try {
            const job = await post('import-jobs/' + activeJob.id + '/resume', {});
            displayJob(job);
            followJob(job.id);
        } catch (error) { status.textContent = error.message; }
        finally { resumeJob.disabled = false; }
    });

    stage.addEventListener('click', async () => {
        if (busy || isParsingFiles || activeJob?.status === 'paused') return;
        if (!root.value.trim()) { status.textContent = '请输入 Confluence 根页面链接或 pageId。'; return; }
        setBusy(true);
        try {
            const job = await post('import-jobs', {root_url: root.value.trim(),
                include_attachments: document.getElementById('confluenceAttachments').checked});
            displayJob(job);
            followJob(job.id);
        } catch (error) {
            setBusy(false);
            status.dataset.state = 'error';
            status.textContent = `创建采集任务失败：${error.message}`;
        }
    });

    // Server-side task lookup also works after refresh, without a browser-only task id.
    stage.disabled = true;
    client.get('/confluence/import-jobs/latest').then(response => {
        if (response.code !== 1) throw new Error(response.msg || '任务查询失败');
        const job = response.data;
        if (job) {
            displayJob(job);
            if (job.status === 'running') followJob(job.id);
        } else { stage.disabled = isParsingFiles; }
    }).catch(() => {
        stage.disabled = isParsingFiles;
        status.textContent = '暂时无法恢复历史采集任务，请刷新后重试。';
    });
});
