(function () {
    'use strict';
    const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    function results(section) {
        let metadata = section.metadata || section.section_metadata || {};
        if (typeof metadata === 'string') {
            try { metadata = JSON.parse(metadata); } catch { return []; }
        }
        return (metadata.mindmap_parsing || []).filter(r => r.tree?.root);
    }
    window.XMindViewer = {
        title(section) {
            const root = results(section)[0]?.tree.root;
            return root ? (root.source_metadata?.title || root.text.split('\n')[0]) : section.section_title;
        },
        render(section) {
            const maps = results(section);
            if (!maps.length) return null;
            let count = 0;
            function node(item, depth, ancestors) {
                count++;
                const label = item.source_metadata?.title || item.text.split('\n')[0];
                const path = [...ancestors, label];
                const children = item.children || [];
                const text = `<span class="xmind-label" title="${escape(path.join(' → '))}">${escape(item.text)}</span>`;
                return `<li>${children.length ? `<details ${depth < 2 ? 'open' : ''}><summary>${text}<small>${children.length} 个分支</small></summary><ul>${children.map(c => node(c, depth + 1, path)).join('')}</ul></details>` : text}</li>`;
            }
            const trees = maps.map(r => `<ul class="xmind-root">${node(r.tree.root, 0, [])}</ul>`).join('');
            return `<div class="xmind-native"><div class="xmind-controls"><span>${count} 个节点 · 点击节点左侧箭头展开或收起</span><button type="button" data-xmind="expand">全部展开</button><button type="button" data-xmind="collapse">收起分支</button><input type="search" class="xmind-search" placeholder="搜索分支…" aria-label="搜索脑图分支"><span class="xmind-search-status" role="status" aria-live="polite"></span></div><div class="xmind-scroll">${trees}</div></div>`;
        }
    };
    const searchStates = new WeakMap();
    function search(viewer, value) {
        const query = value.trim().toLocaleLowerCase();
        const details = [...viewer.querySelectorAll('details')];
        if (query && !searchStates.has(viewer)) {
            searchStates.set(viewer, details.map(detail => [detail, detail.open]));
        }
        const labels = [...viewer.querySelectorAll('.xmind-label')];
        const matches = labels.filter(label => {
            const matched = !!query && label.textContent.toLocaleLowerCase().includes(query);
            label.classList.toggle('xmind-match', matched);
            return matched;
        });
        if (query) {
            for (const label of matches) {
                let ancestor = label.parentElement;
                while (ancestor && ancestor !== viewer) {
                    if (ancestor.tagName === 'DETAILS') ancestor.open = true;
                    ancestor = ancestor.parentElement;
                }
            }
            if (matches.length) {
                const pane = viewer.querySelector('.xmind-scroll');
                pane.scrollTop += matches[0].getBoundingClientRect().top - pane.getBoundingClientRect().top - 16;
            }
        } else if (searchStates.has(viewer)) {
            searchStates.get(viewer).forEach(([detail, open]) => { detail.open = open; });
            searchStates.delete(viewer);
        }
        viewer.querySelector('.xmind-search-status').textContent = query
            ? (matches.length ? `匹配 ${matches.length} 个节点` : '未找到匹配分支') : '';
    }
    document.addEventListener('input', event => {
        if (!event.target.matches('.xmind-search')) return;
        search(event.target.closest('.xmind-native'), event.target.value);
    });
    document.addEventListener('click', event => {
        const button = event.target.closest('[data-xmind]');
        if (!button) return;
        const viewer = button.closest('.xmind-native');
        viewer.querySelectorAll('details').forEach(detail => { detail.open = button.dataset.xmind === 'expand'; });
        viewer.querySelectorAll('.xmind-root > li > details').forEach(detail => { detail.open = true; });
    });
})();
