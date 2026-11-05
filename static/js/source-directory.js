/* DOM-only rendering: source titles are never interpreted as HTML. */
document.addEventListener('DOMContentLoaded', async () => {
    Utils.checkLogin();
    const tree = document.getElementById('tree');
    const status = document.getElementById('status');
    let nodes = [];
    let activeDocumentId = null;
    const button = (label, action) => {
        const element = document.createElement('button');
        element.type = 'button'; element.textContent = label;
        element.addEventListener('click', action); return element;
    };
    function openPage(doc, section) {
        activeDocumentId = String(doc.id);
        const url = new URL('document-detail.html', location.href);
        url.searchParams.set('id', doc.id); url.searchParams.set('source', 'knowledge');
        url.searchParams.set('embedded', '1');
        if (section) {
            url.searchParams.set('sectionId', section.id);
            url.searchParams.set('sectionIndex', section.index);
        }
        document.getElementById('reader').src = url.href;
        document.getElementById('location').textContent = doc.title + (section ? ' / ' + section.title : '');
        tree.querySelectorAll('summary a').forEach(link => {
            if (link.dataset.documentId === String(doc.id)) link.setAttribute('aria-current', 'page');
            else link.removeAttribute('aria-current');
        });
    }
    function render() {
        tree.replaceChildren();
        const query = document.getElementById('filter').value.trim().toLowerCase();
        const byKey = new Map(nodes.map(node => [node.key, node]));
        const visible = new Set();
        nodes.filter(node => !query || node.title.toLowerCase().includes(query)).forEach(node => {
            const visited = new Set();
            while (node && !visited.has(node.key)) {
                visited.add(node.key); visible.add(node.key); node = byKey.get(node.parent);
            }
        });
        function append(node, container, ancestors) {
            if (!visible.has(node.key) || ancestors.has(node.key)) return;
            const next = new Set(ancestors); next.add(node.key);
            const details = document.createElement('details'); details.open = !!query || !node.parent;
            const title = document.createElement('summary'); title.textContent = node.title;
            title.title = node.title;
            details.className = 'page-node';
            details.style.setProperty('--depth', Math.min(ancestors.size, 5));
            details.append(title); container.append(details);
            if (node.documents.length) {
                const actions = document.createElement('div'); actions.className = 'page-actions';
                const sections = document.createElement('div'); sections.className = 'sections';
                let selected = node.documents[0];
                const link = document.createElement('a');
                link.textContent = node.title;
                link.dataset.documentId = selected.id;
                if (String(selected.id) === activeDocumentId) link.setAttribute('aria-current', 'page');
                link.href = 'document-detail.html?' + new URLSearchParams({id: selected.id, source: 'knowledge'});
                link.addEventListener('click', event => {
                    event.preventDefault(); details.open = true; openPage(selected);
                });
                title.replaceChildren(link);
                const renderSections = () => {
                    sections.replaceChildren(...selected.sections.map(s => button(s.title, () => openPage(selected, s))));
                };
                if (node.documents.length > 1) {
                    const select = document.createElement('select'); select.setAttribute('aria-label', '选择导入快照');
                    node.documents.forEach(doc => {
                        const option = document.createElement('option'); option.value = doc.id;
                        option.textContent = `导入快照 #${doc.id}`; select.append(option);
                    });
                    select.addEventListener('change', () => {
                        selected = node.documents.find(doc => String(doc.id) === select.value);
                        renderSections(); openPage(selected);
                    }); actions.append(select);
                }
                renderSections();
                if (selected.sections.length) {
                    const chapterGroup = document.createElement('details');
                    chapterGroup.className = 'chapter-group';
                    const chapterTitle = document.createElement('summary');
                    chapterTitle.textContent = '本页章节';
                    chapterGroup.append(chapterTitle, sections);
                    actions.append(chapterGroup);
                }
                details.append(actions);
            }
            nodes.filter(child => child.parent === node.key).forEach(child => append(child, details, next));
        }
        nodes.filter(node => !byKey.has(node.parent)).forEach(node => append(node, tree, new Set()));
        status.textContent = nodes.length ? (visible.size ? '' : '未找到匹配的页面') : '暂无已导入的 Confluence 页面';
    }
    document.getElementById('filter').addEventListener('input', render);
    try {
        const response = await new APIClient().get('/confluence/directory');
        if (response.code !== 1) throw new Error(response.msg || '目录加载失败');
        nodes = response.data || []; render();
        const requested = new URLSearchParams(location.search);
        const requestedId = requested.get('documentId');
        const first = nodes.find(node => node.documents.some(d => String(d.id) === requestedId)) || nodes.find(node => node.documents.length);
        if (first) {
            const selected = first.documents.find(d => String(d.id) === requestedId) || first.documents[0];
            const section = selected.sections.find(s => String(s.id) === requested.get('sectionId'));
            openPage(selected, section);
            let current = tree.querySelector('a[aria-current="page"]');
            while (current && current !== tree) {
                if (current.tagName === 'DETAILS') current.open = true;
                current = current.parentElement;
            }
        }
    } catch (error) { status.textContent = error.message; }
});
