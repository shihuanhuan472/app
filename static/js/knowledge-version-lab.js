const params = new URLSearchParams(location.search);
const documentId = Number(params.get('documentId'));
const state = { metadata: null, versions: [], branches: [], branchKey: null, selectedId: null };
const $ = id => document.getElementById(id);
const unwrap = response => response && Object.prototype.hasOwnProperty.call(response, 'data') ? response.data : response;
const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const workflowText = {draft:'草稿',pending:'待审核',approved:'审核通过',published:'已发布',rejected:'已驳回',withdrawn:'已撤回',superseded:'已替代',invalidated:'已作废'};
const validityText = {inactive:'未生效',active:'当前有效',legacy_active:'旧设备维护有效',superseded:'已替代',invalidated:'已作废'};

function notify(message) { $('notice').hidden = false; $('notice').textContent = message; }
function branchKey(version) { return (version.hardware_version || '').trim() || '__unassigned__'; }
function branchLabel(key) { return key === '__unassigned__' ? '未指定硬件版本' : key; }
function versionRank(item) { return item.version_major * 100000 + item.version_minor; }
function fileUrl(path) {
    const normalized = String(path || '').replace(/\\/g, '/');
    const match = normalized.match(/(?:^|\/)upload\/(.+)$/i);
    return match ? `/upload/${match[1]}` : '';
}
function formatSize(size) {
    if (!size) return '大小未知';
    if (size < 1024) return `${size} B`;
    if (size < 1048576) return `${(size / 1024).toFixed(1)} KB`;
    return `${(size / 1048576).toFixed(1)} MB`;
}
function summaryCell(label, value) { return `<div class="summary-cell"><span>${escapeHtml(label)}</span><strong title="${escapeHtml(value)}">${escapeHtml(value || '-')}</strong></div>`; }

async function load() {
    if (!documentId) { notify('缺少文档 ID，无法打开试用界面。'); return; }
    $('backLink').href = `knowledge-versions.html?documentId=${documentId}`;
    try {
        const [metadataResponse, versionsResponse] = await Promise.all([
            knowledgeVersionAPI.getMetadata(documentId),
            knowledgeVersionAPI.getVersions(documentId),
        ]);
        state.metadata = unwrap(metadataResponse);
        state.versions = (unwrap(versionsResponse) || []).sort((a, b) => versionRank(b) - versionRank(a));
        buildBranches();
        renderHeader();
        renderBranches();
        const first = state.branches.find(branch => branch.versions.some(item => item.workflow_status === 'published')) || state.branches[0];
        if (first) selectBranch(first.key);
        else notify('当前文档还没有版本。');
    } catch (error) {
        notify(error.message || '版本数据加载失败。');
    }
}

function buildBranches() {
    const groups = new Map();
    state.versions.forEach(version => {
        const key = branchKey(version);
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push(version);
    });
    state.branches = [...groups.entries()].map(([key, versions]) => ({ key, versions }));
    state.branches.sort((left, right) => {
        const leftActive = left.versions.some(item => item.workflow_status === 'published');
        const rightActive = right.versions.some(item => item.workflow_status === 'published');
        return Number(rightActive) - Number(leftActive) || versionRank(right.versions[0]) - versionRank(left.versions[0]);
    });
}

function renderHeader() {
    $('documentTitle').textContent = state.metadata.title || '知识文档';
    $('documentMeta').textContent = `${state.metadata.document_code || '未编号'} · ${state.branches.length} 个硬件分支 · ${state.versions.length} 个版本`;
}

function renderBranches() {
    $('branchList').innerHTML = state.branches.map(branch => {
        const current = branch.versions.find(item => item.workflow_status === 'published');
        return `<button class="branch-button ${branch.key === state.branchKey ? 'selected' : ''}" data-branch="${escapeHtml(branch.key)}"><strong>${escapeHtml(branchLabel(branch.key))}${current ? '<span class="current">使用中</span>' : ''}</strong><span>${branch.versions.length} 个版本${current ? ` · 当前 v${escapeHtml(current.version)}` : ' · 无有效发布版'}</span></button>`;
    }).join('') || '<div class="empty">暂无硬件分支</div>';
    document.querySelectorAll('[data-branch]').forEach(button => button.onclick = () => selectBranch(button.dataset.branch));
}

function selectBranch(key) {
    state.branchKey = key;
    const branch = state.branches.find(item => item.key === key);
    if (!branch) return;
    renderBranches();
    $('workspace').hidden = false;
    $('branchName').textContent = branchLabel(key);
    const preferred = branch.versions.find(item => item.workflow_status === 'published') || branch.versions[0];
    renderVersionList(branch.versions, preferred.id);
    selectVersion(preferred.id);
}

function renderVersionList(versions, selectedId) {
    $('versionList').innerHTML = versions.map(version => `<button class="version-button ${version.id === selectedId ? 'selected' : ''}" data-version-id="${version.id}"><strong>v${escapeHtml(version.version)}</strong><span class="status ${escapeHtml(version.workflow_status)}">${escapeHtml(workflowText[version.workflow_status] || version.workflow_status)}</span><small>${escapeHtml(version.change_summary || '未填写变更摘要')}</small></button>`).join('');
    document.querySelectorAll('[data-version-id]').forEach(button => button.onclick = () => selectVersion(Number(button.dataset.versionId)));
}

async function selectVersion(versionId) {
    state.selectedId = versionId;
    document.querySelectorAll('[data-version-id]').forEach(button => button.classList.toggle('selected', Number(button.dataset.versionId) === versionId));
    $('snapshotTitle').textContent = '正在加载版本快照...';
    try {
        const version = unwrap(await knowledgeVersionAPI.getVersion(versionId));
        if (state.selectedId !== versionId) return;
        renderSnapshot(version);
    } catch (error) {
        notify(error.message || '版本快照加载失败。');
    }
}

function renderSnapshot(version) {
    $('snapshotBranch').textContent = branchLabel(branchKey(version));
    $('snapshotTitle').textContent = `v${version.version} · ${version.title || state.metadata.title}`;
    $('snapshotStatus').innerHTML = `<span class="status ${escapeHtml(version.workflow_status)}">${escapeHtml(workflowText[version.workflow_status] || version.workflow_status)}</span>`;
    $('snapshotSummary').innerHTML = [
        summaryCell('有效状态', validityText[version.validity_status] || version.validity_status),
        summaryCell('变更摘要', version.change_summary),
        summaryCell('变更原因', version.change_reason || '未填写'),
        summaryCell('变更级别', version.change_category),
        summaryCell('索引状态', version.index_status),
        summaryCell('发布时间', version.published_at ? new Date(version.published_at).toLocaleString() : '尚未发布'),
    ].join('');
    renderFiles(version.files || []);
    renderSections(version.sections || []);
}

function renderFiles(files) {
    $('fileCount').textContent = `${files.length} 个文件`;
    $('fileList').innerHTML = files.map(file => {
        const url = fileUrl(file.storage_path);
        const tag = url ? 'a' : 'div';
        const attrs = url ? `href="${escapeHtml(url)}" target="_blank" rel="noopener"` : '';
        return `<${tag} class="file-item" ${attrs}><span class="file-icon"><i class="fas ${file.file_role === 'primary' ? 'fa-file-lines' : 'fa-paperclip'}"></i></span><span class="file-copy"><strong>${escapeHtml(file.original_name)}</strong><span>${file.file_role === 'primary' ? '主文件' : '附件'} · ${formatSize(file.file_size)}${file.legacy_fallback ? ' · 旧数据映射' : ''}</span></span>${url ? '<i class="fas fa-arrow-up-right-from-square"></i>' : ''}</${tag}>`;
    }).join('') || '<div class="empty">这个版本没有保存源文件快照</div>';
}

function renderSections(sections) {
    $('sectionCount').textContent = `${sections.length} 个章节`;
    $('sectionList').innerHTML = sections.map((section, index) => `<div class="section-item"><button class="section-toggle"><span><strong>${escapeHtml(section.section_title || `章节 ${index + 1}`)}</strong><br><span>${escapeHtml(section.stable_section_key || '')}</span></span><i class="fas fa-chevron-down"></i></button><div class="section-body">${escapeHtml(section.plain_text || '暂无正文')}</div></div>`).join('') || '<div class="empty">这个版本没有章节快照</div>';
    document.querySelectorAll('.section-toggle').forEach(button => button.onclick = () => button.parentElement.classList.toggle('open'));
}

document.addEventListener('DOMContentLoaded', load);
