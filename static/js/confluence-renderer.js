/* Render typed source blocks, never execute/render remote HTML. */
window.ConfluenceRenderer = (() => {
    const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
    function mergeExcelSections(sections) {
        const output = [], sheets = new Map();
        for (const section of sections || []) {
            const meta = section.metadata || section.section_metadata || {};
            if (meta.chunk_strategy !== 'excel_records_v1' || !Array.isArray(meta.records)) {
                output.push(section); continue;
            }
            const key = JSON.stringify([meta.site, meta.page_id, meta.attachment_id, meta.sheet_name, meta.headers]);
            if (!sheets.has(key)) {
                const merged = {...section, section_title: meta.sheet_name || section.section_title,
                    metadata: {...meta, records: [], display_section_ids: [], display_section_indices: []}};
                sheets.set(key, merged); output.push(merged);
            }
            const merged = sheets.get(key);
            merged.metadata.records.push(...meta.records);
            merged.metadata.display_section_ids.push(String(section.id));
            merged.metadata.display_section_indices.push(String(section.section_index));
            merged.metadata.row_start = Math.min(merged.metadata.row_start, meta.row_start);
            merged.metadata.row_end = Math.max(merged.metadata.row_end, meta.row_end);
        }
        for (const section of sheets.values()) {
            section.metadata.records.sort((a, b) => a.row - b.row);
        }
        return output;
    }
    function renderExcel(section) {
        const meta = section.metadata || section.section_metadata || {};
        if (meta.chunk_strategy !== 'excel_records_v1' || !Array.isArray(meta.records)) return null;
        const headers = meta.headers || [];
        function values(record) {
            if (Array.isArray(record.values)) return record.values;
            // Older parsed snapshots stored labeled text instead of cell arrays.
            const lines = String(record.text || '').split('\n').slice(1);
            let cursor = 0;
            return headers.map((header, index) => {
                const prefix = header + '：';
                const start = lines.findIndex((line, i) => i >= cursor && line.startsWith(prefix));
                if (start < 0) return '';
                let end = lines.length;
                if (index + 1 < headers.length) {
                    const found = lines.findIndex((line, i) => i > start && line.startsWith(headers[index + 1] + '：'));
                    if (found >= 0) end = found;
                }
                cursor = end;
                return [lines[start].slice(prefix.length), ...lines.slice(start + 1, end)].join('\n');
            });
        }
        return `<p style="color:#607286;font-size:14px">${escape(meta.sheet_name)} · 原始行 ${escape(meta.row_start)}–${escape(meta.row_end)}</p>` +
            `<div style="overflow-x:auto;background:white;border:1px solid #dce4ee;border-radius:8px"><table style="border-collapse:collapse;width:100%;font-size:14px"><thead><tr>` +
            ['原始行号', ...headers].map(h => `<th style="position:sticky;top:0;background:#edf3fa;padding:10px;border:1px solid #dce4ee;text-align:left;white-space:nowrap">${escape(h)}</th>`).join('') +
            '</tr></thead><tbody>' + meta.records.map((record, index) => `<tr style="background:${index % 2 ? '#f7f9fc' : 'white'}">` +
                [record.row, ...values(record)].map(v => `<td style="padding:9px 12px;border:1px solid #e2e8f0;white-space:pre-wrap;min-width:80px;max-width:400px;overflow-wrap:anywhere">${escape(v)}</td>`).join('') + '</tr>').join('') + '</tbody></table></div>';
    }
    function render(section, imageRenderer) {
        const metadata = section.metadata || section.section_metadata || {};
        if (metadata.source_type !== 'confluence' || !Array.isArray(metadata.content_blocks)) return null;
        // Edited text is authoritative; do not replace it with an old source snapshot.
        const original = metadata.content_blocks.map(b => b.text || '').join('\n\n').trim();
        let storedText = String(section.plain_text || '').trim().replace(/^页面路径：[^\n]*\n/, '');
        if (metadata.chunk_strategy === 'ppt_slide_v1' && metadata.attachment_title) {
            const suffix = '\n附件名称：' + metadata.attachment_title;
            if (storedText.endsWith(suffix)) storedText = storedText.slice(0, -suffix.length).trim();
        }
        if (storedText !== original) return null;
        const images = section.image_urls || [];
        function linkedText(value) {
            const input = String(value || '');
            const links = (metadata.source_links || []).filter(link => {
                try { return ['http:', 'https:'].includes(new URL(link.url).protocol); }
                catch (_) { return false; }
            }).map(link => ({...link, token: `${link.title || ''} (${link.url})`}))
              .sort((a, b) => b.token.length - a.token.length);
            let output = '', offset = 0;
            while (offset < input.length) {
                let found = null, position = input.length;
                for (const link of links) {
                    const index = input.indexOf(link.token, offset);
                    if (index >= 0 && index < position) { position = index; found = link; }
                }
                output += escape(input.slice(offset, position)).replace(/\n/g, '<br>');
                if (!found) break;
                output += `<a class="confluence-page-link" href="${escape(found.url)}" data-site="${escape(metadata.site)}" data-root-id="${escape(metadata.root_id)}" target="_blank" rel="noopener noreferrer">${escape(found.title || found.url)}</a>`;
                offset = position + found.token.length;
            }
            return output;
        }
        function text(value) {
            let result = '';
            const input = String(value || '');
            const pattern = /\[图片：([^\]]+)\]/g;
            let match, offset = 0;
            while ((match = pattern.exec(input))) {
                result += linkedText(input.slice(offset, match.index));
                const index = images.indexOf(match[1]);
                result += index >= 0 ? imageRenderer(match[1], index + 1) : escape(match[0]);
                offset = pattern.lastIndex;
            }
            return result + linkedText(input.slice(offset));
        }
        const source = metadata.title_path?.length
            ? `<p>${escape(metadata.title_path.join(' / '))}</p>` : '';
        const body = metadata.content_blocks.map(block => {
            if (block.type === 'heading') {
                const level = Math.max(2, Math.min(6, Number(block.level) + 1 || 3));
                return `<h${level}>${text(block.text)}</h${level}>`;
            }
            if (block.type !== 'table') return `<div style="white-space:pre-wrap;margin:8px 0;">${text(block.text)}</div>`;
            let rows = '';
            if (block.row_range) {
                rows = '<tr>' + (block.headers || []).map(h => `<th>${escape(h)}</th>`).join('') + '</tr>';
                rows += (block.rows || []).slice(...block.row_range).map(row => '<tr>' + row.map(c => `<td>${text(c)}</td>`).join('') + '</tr>').join('');
            } else {
                for (let r = 0; r < (block.rows || []).length; r++) {
                    rows += '<tr>' + (block.cells || []).filter(c => c.row === r).map(c => {
                        const tag = c.header ? 'th' : 'td';
                        const rs = Math.max(1, Math.min(2000, Number(c.rowspan) || 1));
                        const cs = Math.max(1, Math.min(100, Number(c.colspan) || 1));
                        return `<${tag} rowspan="${rs}" colspan="${cs}">${text(c.text)}</${tag}>`;
                    }).join('') + '</tr>';
                }
            }
            return `<div style="overflow:auto"><table border="1" cellpadding="8" style="border-collapse:collapse;width:100%">${rows}</table></div>`;
        }).join('');
        const warnings = (metadata.parse_warnings || []).map(w => `<p style="color:#a65b00">${escape(w)}</p>`).join('');
        const attachments = (metadata.source_links || []).filter(link =>
            link.type === 'attachment' && link.filename && original.includes(link.filename)
            && !link.page_title);
        const targets = [...new Set(attachments.map(link => link.filename))].map(name =>
            `<span class="confluence-attachment-result" data-site="${escape(metadata.site)}" data-page-id="${escape(metadata.page_id)}" data-filename="${escape(name)}"></span>`).join('');
        const fallback = `<span class="confluence-attachment-result" data-site="${escape(metadata.site)}" data-page-id="${escape(metadata.page_id)}" data-section-text="${escape(original)}"></span>`;
        return `<div class="confluence-source-section">${source}${body}${targets || fallback}${warnings}</div>`;
    }
    async function linkAttachments(container, client) {
        const pageLinks = [...container.querySelectorAll('.confluence-page-link')];
        if (pageLinks.length) {
            // Resolve only already imported pages; never fetch a linked website.
            client.get('/confluence/directory').then(response => {
                if (response.code !== 1) return;
                const nodes = response.data || [];
                const byKey = new Map(nodes.map(node => [JSON.stringify(JSON.parse(node.key)), node]));
                pageLinks.forEach(link => {
                    const url = new URL(link.href);
                    let site;
                    try { site = new URL(link.dataset.site); } catch (_) { return; }
                    if (url.origin !== site.origin || !link.dataset.rootId) return;
                    const pageId = url.searchParams.get('pageId') || url.pathname.match(/\/pages\/(\d+)(?:\/|$)/)?.[1];
                    if (!pageId) return;
                    const node = byKey.get(JSON.stringify([link.dataset.site, pageId]));
                    if (!node?.documents?.length) return;
                    const rootKey = JSON.stringify([link.dataset.site, link.dataset.rootId]);
                    let ancestor = node;
                    const seen = new Set();
                    while (ancestor && !seen.has(ancestor.key)) {
                        seen.add(ancestor.key);
                        if (JSON.stringify(JSON.parse(ancestor.key)) === rootKey) {
                            link.href = 'document-detail.html?' + new URLSearchParams({id: node.documents[0].id, source: 'knowledge', embedded: '1'});
                            link.target = '_self';
                            return;
                        }
                        ancestor = ancestor.parent && byKey.get(JSON.stringify(JSON.parse(ancestor.parent)));
                    }
                });
            }).catch(error => console.warn('页面链接关联失败，保留原地址', error));
        }
        const groups = new Map();
        container.querySelectorAll('.confluence-attachment-result').forEach(target => {
            const key = JSON.stringify([target.dataset.site, target.dataset.pageId]);
            if (!groups.has(key)) groups.set(key, []);
            groups.get(key).push(target);
        });
        await Promise.all([...groups].map(async ([key, targets]) => {
            const [site, pageId] = JSON.parse(key);
            try {
                const response = await client.get('/confluence/parsed-attachments?' + new URLSearchParams({site, page_id: pageId}));
                if (response.code !== 1) return;
                targets.forEach(target => {
                    const matches = (response.data || []).filter(item => target.dataset.filename
                        ? item.title === target.dataset.filename
                        : target.dataset.sectionText?.includes(item.title));
                    target.replaceChildren();
                    matches.forEach(match => {
                    if (!Number.isSafeInteger(match.document_id) || match.document_id <= 0) return;
                    const link = document.createElement('a');
                    link.href = 'document-detail.html?' + new URLSearchParams({id: match.document_id, source: 'knowledge'});
                    link.textContent = `${match.title} · 查看解析结果`;
                    link.target = '_blank'; link.rel = 'noopener noreferrer';
                    const section = target.closest('.confluence-source-section');
                    let linked = false;
                    if (section) {
                        const walker = document.createTreeWalker(section, NodeFilter.SHOW_TEXT);
                        const texts = [];
                        while (walker.nextNode()) {
                            const node = walker.currentNode;
                            if (!node.parentElement.closest('a, .confluence-attachment-result') && node.nodeValue.includes(match.title)) texts.push(node);
                        }
                        for (const node of texts) {
                            const parts = node.nodeValue.split(match.title);
                            const fragment = document.createDocumentFragment();
                            parts.forEach((part, index) => {
                                if (index) {
                                    const inlineLink = link.cloneNode(false);
                                    inlineLink.textContent = match.title;
                                    inlineLink.title = '查看附件解析结果';
                                    fragment.append(inlineLink);
                                }
                                fragment.append(document.createTextNode(part));
                            });
                            node.replaceWith(fragment);
                            linked = true;
                        }
                    }
                    if (!linked) {
                        const paragraph = document.createElement('p'); paragraph.append(link); target.append(paragraph);
                    }
                    });
                });
            } catch (error) { console.warn('附件解析结果链接加载失败', error); }
        }));
    }
    return {render, renderExcel, mergeExcelSections, linkAttachments};
})();
