/* Mindmap results are supplied by section.metadata.image_parsing. */
(function () {
    'use strict';
    const escape = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
    const normalize = value => String(value || '').replace(/\\/g, '/').replace(/^\/+/, '');

    window.MindmapViewer = {
        render(section, url, index, assetUrl) {
            const results = (section.metadata || section.section_metadata || {}).image_parsing;
            const result = Array.isArray(results) && results.find(item => normalize(item.image_url) === normalize(url));
            if (!result || !['parsed', 'needs_review'].includes(result.status) || !result.tree?.root) return null;
            const tree = result.tree;
            const boxes = tree._meta?.boxes || [];
            let count = 0;
            const renderNode = (node, depth, ancestors) => {
                count++;
                const path = [...ancestors, node.text || ''];
                const boxIndex = /^n_(\d+)$/.exec(node.id || '');
                const box = boxIndex ? boxes[Number(boxIndex[1])] : null;
                const coords = node.box ? node.box.join(',') : box ? [box.x0, box.y0, box.x1, box.y1].join(',') : '';
                const button = `<button type="button" class="mindmap-node" data-box="${escape(coords)}" data-path="${escape(path.join(' → '))}" aria-pressed="false">${escape(node.text)}${node.flag === 'orphan' ? ' <span class="mindmap-warning">待核对</span>' : ''}</button>`;
                const children = Array.isArray(node.children) ? node.children : [];
                return `<li>${children.length ? `<details class="mindmap-branch" ${depth < 2 ? 'open' : ''}><summary>${button}</summary><ul>${children.map(child => renderNode(child, depth + 1, path)).join('')}</ul></details>` : button}</li>`;
            };
            const nodes = renderNode(tree.root, 0, []);
            const fullUrl = assetUrl(url);
            // Only accept web image URLs; never expose result-file paths as links.
            const safeUrl = /^(https?:\/\/|\/)/i.test(fullUrl) ? fullUrl : '';
            return `<section class="mindmap-viewer" aria-label="脑图解析结果">
                <div class="mindmap-heading"><strong>脑图解析 · 图片 ${index}</strong><span>${count} 个节点 · ${result.human_edited ? '已人工修订' : result.status === 'needs_review' ? '部分关系待核对' : '自动解析，请核对'}</span></div>
                <div class="mindmap-toolbar">
                    <label>搜索节点 <input type="search" class="mindmap-search" placeholder="输入节点文字" aria-label="搜索脑图节点"></label>
                    <button type="button" data-mindmap-action="expand">全部展开</button>
                    <button type="button" data-mindmap-action="collapse">收起分支</button>
                    <button type="button" data-mindmap-action="text" aria-expanded="false">解析文本</button>
                    <a href="${escape(safeUrl)}" target="_blank" rel="noopener noreferrer">查看原图</a>
                </div>
                <div class="mindmap-layout">
                    <div class="mindmap-image-pane"><div class="mindmap-image-stage"><img src="${escape(safeUrl)}" loading="lazy" alt="脑图原图 ${index}"><span class="mindmap-highlight" hidden></span></div><p class="mindmap-image-error" hidden>原图加载失败，请尝试查看原图。</p></div>
                    <div class="mindmap-tree-pane"><p class="mindmap-search-status" aria-live="polite">点击节点可定位原图文字</p><ul class="mindmap-tree">${nodes}</ul></div>
                </div>
                <p class="mindmap-path" aria-live="polite">当前未选择节点</p>
                <pre class="mindmap-text" hidden>${escape(result.markdown || '')}</pre>
            </section>`;
        }
    };

    function highlight(viewer) {
        const selected = viewer.querySelector('.mindmap-node[aria-pressed="true"]');
        const img = viewer.querySelector('.mindmap-image-stage img');
        const marker = viewer.querySelector('.mindmap-highlight');
        const coords = (selected?.dataset.box || '').split(',').map(Number);
        marker.hidden = !selected?.dataset.box || !img.naturalWidth || coords.length !== 4 || !coords.every(Number.isFinite);
        if (marker.hidden) return;
        const [x0, y0, x1, y1] = coords;
        marker.style.left = `${x0 / img.naturalWidth * 100}%`;
        marker.style.top = `${y0 / img.naturalHeight * 100}%`;
        marker.style.width = `${(x1 - x0) / img.naturalWidth * 100}%`;
        marker.style.height = `${(y1 - y0) / img.naturalHeight * 100}%`;
    }

    document.addEventListener('click', event => {
        const viewer = event.target.closest('.mindmap-viewer');
        if (!viewer) return;
        const node = event.target.closest('.mindmap-node');
        if (node) {
            event.preventDefault();
            viewer.querySelectorAll('.mindmap-node').forEach(button => button.setAttribute('aria-pressed', String(button === node)));
            viewer.querySelector('.mindmap-path').textContent = node.dataset.path;
            highlight(viewer);
            const marker = viewer.querySelector('.mindmap-highlight');
            if (!marker.hidden) {
                const pane = viewer.querySelector('.mindmap-image-pane');
                pane.scrollTop = marker.offsetTop - pane.clientHeight / 2;
                pane.scrollLeft = marker.offsetLeft - pane.clientWidth / 2;
            }
            return;
        }
        const action = event.target.closest('[data-mindmap-action]');
        if (!action) return;
        if (action.dataset.mindmapAction === 'text') {
            const text = viewer.querySelector('.mindmap-text');
            text.hidden = !text.hidden;
            action.setAttribute('aria-expanded', String(!text.hidden));
            action.textContent = text.hidden ? '解析文本' : '收起文本';
            return;
        }
        viewer.querySelectorAll('.mindmap-branch').forEach((branch, index) => {
            branch.open = action.dataset.mindmapAction === 'expand' || index === 0;
        });
    });

    document.addEventListener('input', event => {
        if (!event.target.matches('.mindmap-search')) return;
        const viewer = event.target.closest('.mindmap-viewer');
        const query = event.target.value.trim().toLocaleLowerCase();
        let matches = 0;
        viewer.querySelectorAll('.mindmap-node').forEach(node => {
            const match = !!query && node.textContent.toLocaleLowerCase().includes(query);
            node.classList.toggle('mindmap-match', match);
            if (match) {
                matches++;
                for (let parent = node.parentElement; parent && parent !== viewer; parent = parent.parentElement) {
                    if (parent.matches('details')) parent.open = true;
                }
            }
        });
        viewer.querySelector('.mindmap-search-status').textContent = query ? `找到 ${matches} 个匹配节点（已展开所在分支）` : '点击节点可定位原图文字';
    });
    document.addEventListener('load', event => {
        if (event.target.matches?.('.mindmap-image-stage img')) highlight(event.target.closest('.mindmap-viewer'));
    }, true);
    document.addEventListener('error', event => {
        if (!event.target.matches?.('.mindmap-image-stage img')) return;
        const viewer = event.target.closest('.mindmap-viewer');
        viewer.querySelector('.mindmap-image-error').hidden = false;
        viewer.querySelector('.mindmap-highlight').hidden = true;
    }, true);
})();
