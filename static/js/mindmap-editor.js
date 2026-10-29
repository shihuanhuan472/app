/* Editing is local until the enclosing document form saves and reindexes. */
(function () {
    'use strict';
    const copy = value => JSON.parse(JSON.stringify(value));
    const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
    const normalize = value => String(value || '').replace(/\\/g, '/').replace(/^\/+/, '');
    window.MindmapEditor = {
        has(section, url) {
            return (section.metadata?.image_parsing || []).some(r => normalize(r.image_url) === normalize(url) && r.tree?.root && ['parsed', 'needs_review'].includes(r.status));
        },
        open(section, url, assetUrl, onApply) {
            const original = section.metadata.image_parsing.find(r => normalize(r.image_url) === normalize(url));
            if (!original?.tree?.root) return;
            let result = copy(original), selected = result.tree.root.id, zoom = 100, changed = false;
            const undo = [], redo = [];
            const dialog = document.createElement('dialog');
            dialog.className = 'mindmap-edit-dialog';
            dialog.innerHTML = `<div class="editor-shell"><header><strong>编辑脑图</strong><button type="button" data-action="undo">撤销</button><button type="button" data-action="redo">重做</button><button type="button" data-action="close">取消</button><button type="button" data-action="apply">应用到章节</button></header>
                <div class="mindmap-edit-tools"><label>缩放 <input class="editor-zoom" type="range" min="50" max="400" value="100"></label><button type="button" data-action="locate">定位选中节点</button><label>搜索 <input type="search" class="editor-search" placeholder="节点文字"></label><button type="button" data-action="expand">全部展开</button><button type="button" data-action="collapse">收起分支</button></div>
                <div class="editor-image"><div class="mindmap-image-stage"><img alt="脑图原图"><span class="mindmap-highlight" hidden></span></div></div>
                <div class="editor-bottom"><p class="editor-status" aria-live="polite"></p><div class="editor-form"><label for="mindmap-node-text">节点文字</label><input id="mindmap-node-text" class="editor-text"><button type="button" data-action="rename">修改文字</button><button type="button" data-action="child">添加子节点</button><button type="button" data-action="sibling">添加同级</button><button type="button" data-action="delete">删除分支</button><button type="button" data-action="lift">删除节点，子节点上移</button></div>
                <div class="editor-form"><label>移动到 <select class="editor-parent"></select></label><button type="button" data-action="move">确认移动</button><button type="button" data-action="verify">标记节点已核对</button></div><ul class="editor-tree"></ul></div>
                <footer>应用后请点击章节页“保存修改”，同步更新展示内容与检索数据。原图保持原样。</footer></div>`;
            document.body.appendChild(dialog);
            const $ = selector => dialog.querySelector(selector);
            const img = $('.editor-image img');
            img.src = assetUrl(url);
            function entries() {
                const all = [];
                function visit(node, parent, path) {
                    const next = [...path, node.text];
                    all.push({node, parent, path: next});
                    (node.children || []).forEach(child => visit(child, node, next));
                }
                visit(result.tree.root, null, []);
                return all;
            }
            const current = () => entries().find(e => e.node.id === selected);
            function checkpoint() { undo.push(copy(result)); if (undo.length > 100) undo.shift(); redo.length = 0; changed = true; }
            function locate() {
                const node = current()?.node;
                const index = /^n_(\d+)$/.exec(node?.id || '');
                const raw = index ? result.tree._meta?.boxes?.[Number(index[1])] : null;
                const box = node?.box || (raw ? [raw.x0, raw.y0, raw.x1, raw.y1] : null);
                const marker = $('.mindmap-highlight');
                marker.hidden = !box || !img.naturalWidth;
                if (marker.hidden) return;
                const [x0,y0,x1,y1] = box;
                Object.assign(marker.style, {left: `${x0/img.naturalWidth*100}%`, top: `${y0/img.naturalHeight*100}%`, width: `${(x1-x0)/img.naturalWidth*100}%`, height: `${(y1-y0)/img.naturalHeight*100}%`});
                const pane = $('.editor-image');
                pane.scrollTop = (y0+y1)/2/img.naturalHeight*img.clientHeight-pane.clientHeight/2;
                pane.scrollLeft = (x0+x1)/2/img.naturalWidth*img.clientWidth-pane.clientWidth/2;
            }
            function select() {
                const entry = current();
                if (!entry) return;
                $('.editor-text').value = entry.node.text;
                $('.editor-status').textContent = entry.path.join(' → ') + (entry.node.reviewed ? ' · 已核对' : '');
                $('.editor-tree').querySelectorAll('[data-node]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.node === selected)));
                const forbidden = new Set();
                function descendants(node) { forbidden.add(node.id); (node.children || []).forEach(descendants); }
                descendants(entry.node);
                $('.editor-parent').innerHTML = entries().filter(e => !forbidden.has(e.node.id)).map(e => `<option value="${esc(e.node.id)}">${esc(e.path.join(' → '))}</option>`).join('');
                if (entry.parent) $('.editor-parent').value = entry.parent.id;
                ['sibling','delete','lift','move'].forEach(action => $(`[data-action="${action}"]`).disabled = !entry.parent);
                locate();
            }
            function search() {
                const query = $('.editor-search').value.trim().toLocaleLowerCase();
                $('.editor-tree').querySelectorAll('[data-node]').forEach(button => {
                    const match = !!query && button.textContent.toLocaleLowerCase().includes(query);
                    button.classList.toggle('match', match);
                    if (match) for (let p=button.parentElement; p && p!==dialog; p=p.parentElement) if(p.matches('details')) p.open=true;
                });
            }
            function render() {
                const opened = new Set([...$('.editor-tree').querySelectorAll('details[open]')].map(e=>e.dataset.id));
                function nodeHtml(node, depth) {
                    const children = node.children || [];
                    const button = `<button type="button" data-node="${esc(node.id)}" aria-pressed="false">${esc(node.text)}${node.reviewed ? ' ✓' : node.flag ? ' · 待核对' : ''}</button>`;
                    return `<li>${children.length ? `<details data-id="${esc(node.id)}" ${opened.has(node.id)||depth<2 ? 'open' : ''}><summary>${button}</summary><ul>${children.map(child=>nodeHtml(child,depth+1)).join('')}</ul></details>` : button}</li>`;
                }
                $('.editor-tree').innerHTML = nodeHtml(result.tree.root,0);
                $('[data-action="undo"]').disabled = !undo.length;
                $('[data-action="redo"]').disabled = !redo.length;
                select(); search();
            }
            function close() { if (!changed || confirm('放弃本次尚未应用的脑图修改？')) { dialog.close(); dialog.remove(); } }
            dialog.addEventListener('cancel', event => { event.preventDefault(); close(); });
            img.addEventListener('load', locate);
            img.addEventListener('error', () => { $('.editor-status').textContent = '原图加载失败，可继续编辑节点'; });
            $('.editor-zoom').addEventListener('input', event => { zoom=Number(event.target.value); $('.mindmap-image-stage').style.width=`${zoom}%`; locate(); });
            $('.editor-search').addEventListener('input', search);
            $('.editor-text').addEventListener('keydown', event => { if(event.key==='Enter') { event.preventDefault(); $('[data-action="rename"]').click(); } });
            dialog.addEventListener('dblclick', event => { if(event.target.closest('[data-node]')) { $('.editor-text').focus(); $('.editor-text').select(); } });
            dialog.addEventListener('click', event => {
                const button = event.target.closest('[data-node]');
                if (button) { event.preventDefault(); selected=button.dataset.node; select(); return; }
                const action = event.target.closest('[data-action]')?.dataset.action;
                const entry = current();
                if (!action || !entry) return;
                if (action==='close') return close();
                if (action==='locate') return locate();
                if (action==='expand'||action==='collapse') { $('.editor-tree').querySelectorAll('details').forEach((e,i)=>e.open=action==='expand'||i===0); return; }
                if (action==='undo'||action==='redo') {
                    const from=action==='undo'?undo:redo, to=action==='undo'?redo:undo;
                    if (!from.length) return;
                    to.push(copy(result)); result=from.pop(); changed=true;
                    if(!entries().some(e=>e.node.id===selected)) selected=result.tree.root.id;
                    render(); return;
                }
                if (action==='apply') {
                    result.human_edited=true;
                    const target = section.metadata.image_parsing.indexOf(original);
                    section.metadata.image_parsing[target]=copy(result);
                    onApply?.(); dialog.close(); dialog.remove(); return;
                }
                if (action==='rename') {
                    const text=$('.editor-text').value.trim();
                    if(!text) { alert('节点文字不能为空'); return; }
                    checkpoint(); entry.node.text=text;
                } else if(action==='child'||action==='sibling') {
                    const text=prompt('新节点文字'); if(!text?.trim()) return;
                    if(action==='sibling'&&!entry.parent) return;
                    checkpoint(); const node={id:'manual_'+crypto.randomUUID(),text:text.trim(),children:[],role:'unknown'};
                    const parent=action==='child'?entry.node:entry.parent;
                    (parent.children ||= []).push(node); selected=node.id;
                } else if(action==='delete'||action==='lift') {
                    if(!entry.parent || !confirm(action==='delete'?'删除选中节点及其全部子节点？':'删除选中节点，并将其子节点上移到父节点？')) return;
                    checkpoint(); const siblings=entry.parent.children, index=siblings.indexOf(entry.node);
                    siblings.splice(index,1,...(action==='lift'?entry.node.children||[]:[])); selected=entry.parent.id;
                } else if(action==='move') {
                    const target=entries().find(e=>e.node.id===$('.editor-parent').value);
                    if(!entry.parent||!target||!confirm(`将“${entry.node.text}”移动到：${target.path.join(' → ')}？`)) return;
                    checkpoint(); entry.parent.children.splice(entry.parent.children.indexOf(entry.node),1); (target.node.children ||= []).push(entry.node);
                } else if(action==='verify') {
                    checkpoint(); entry.node.reviewed=true; delete entry.node.flag;
                    const index=/^n_(\d+)$/.exec(entry.node.id);
                    if(index) for(const key of ['orphans','detached']) if(result.tree._meta?.[key]) result.tree._meta[key]=result.tree._meta[key].filter(i=>i!==Number(index[1]));
                } else return;
                render();
            });
            render(); dialog.showModal();
        }
    };
})();
