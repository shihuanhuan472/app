const assert = require('node:assert/strict');
global.window = {};
require('../static/js/confluence-renderer.js');
const blocks = [
    {type: 'paragraph', text: '先断电[图片：upload/images/a.png]再拆盖'},
    {type: 'table', text: '配置：RUO', rows: [['RUO'], ['RUO']], cells: [
        {row: 0, column: 0, rowspan: 2, colspan: 1, text: 'RUO', header: false}
    ]}
];
const section = {plain_text: '页面路径：根 / 子\n' + blocks.map(b => b.text).join('\n\n'), image_urls: ['upload/images/a.png'],
    metadata: {source_type: 'confluence', source_url: 'https://wiki.test/pages/2',
        title_path: ['根', '<script>'], heading_path: ['操作', '拆卸'],
        content_blocks: blocks, parse_warnings: ['<unsafe>']}};
const html = window.ConfluenceRenderer.render(section, () => '<img data-test="image">');
assert.ok(html.includes('rowspan="2"'));
assert.ok(html.indexOf('先断电') < html.indexOf('<img'));
assert.ok(html.indexOf('<img') < html.indexOf('再拆盖'));
assert.ok(!html.includes('<script>'));
assert.ok(html.includes('&lt;unsafe&gt;'));
assert.ok(html.includes('根 / &lt;script&gt;'));
assert.ok(!html.includes('查看 Confluence 原文'));
assert.ok(!html.includes('href="https://wiki.test/pages/2"'));
assert.equal(window.ConfluenceRenderer.render({...section, plain_text: '编辑后的正文'}, () => ''), null);
const large = {type: 'table', text: '第2行', headers: ['型号'], rows: [['型号'], ['A'], ['B']], row_range: [2, 3]};
const result = window.ConfluenceRenderer.render({plain_text: '第2行', metadata: {source_type: 'confluence', content_blocks: [large]}}, () => '');
assert.ok(result.includes('<td>B</td>'));
assert.ok(!result.includes('<td>A</td>'));
console.log('Confluence renderer checks passed');
const attachmentSection = {plain_text: '风险清单.xlsx', metadata: {
    source_type: 'confluence', site: 'https://wiki.test', page_id: '2',
    content_blocks: [{type: 'paragraph', text: '风险清单.xlsx'}],
    source_links: [{type: 'attachment', filename: '风险清单.xlsx'},
                   {type: 'attachment', filename: '其他.xlsx'}]
}};
const attachmentHtml = window.ConfluenceRenderer.render(attachmentSection, () => '');
const externalText = '培训视频 (https://example.test/training?a=1&b=2)';
const externalHtml = window.ConfluenceRenderer.render({plain_text: externalText, metadata: {
    source_type: 'confluence', content_blocks: [{type: 'paragraph', text: externalText}],
    source_links: [{type: 'link', title: '培训视频', url: 'https://example.test/training?a=1&b=2'}]
}}, () => '');
assert.ok(externalHtml.includes('href="https://example.test/training?a=1&amp;b=2"'));
assert.ok(externalHtml.includes('>培训视频</a>'));
assert.ok(externalHtml.includes('rel="noopener noreferrer"'));
assert.ok(attachmentHtml.includes('data-filename="风险清单.xlsx"'));
assert.ok(!attachmentHtml.includes('data-filename="其他.xlsx"'));
assert.ok(attachmentHtml.includes('data-page-id="2"'));
const excelMeta = {chunk_strategy: 'excel_records_v1', sheet_name: 'Sheet1', row_start: 2, row_end: 2,
    headers: ['SN', '备注'], records: [{row: 2, text: '原始行号：2\nSN：ABC\n备注：<script>\n第二行'}]};
const excelHtml = window.ConfluenceRenderer.renderExcel({metadata: excelMeta});
assert.ok(excelHtml.includes('ABC'));
assert.ok(excelHtml.includes('&lt;script&gt;\n第二行'));
assert.ok(!excelHtml.includes('<script>'));
assert.ok(excelHtml.includes('<table'));
assert.ok(excelHtml.includes('原始行号'));
excelMeta.records[0].values = ['XYZ', ''];
assert.ok(window.ConfluenceRenderer.renderExcel({metadata: excelMeta}).includes('XYZ'));
assert.equal(window.ConfluenceRenderer.renderExcel({metadata: {}}), null);
const originalSections = [
    {id: 1, section_index: 0, metadata: {...excelMeta, records: [{row: 2, values: ['A', '']}]}},
    {id: 2, section_index: 1, metadata: {...excelMeta, row_start: 3, row_end: 3, records: [{row: 3, values: ['B', '']}]}}
];
const merged = window.ConfluenceRenderer.mergeExcelSections(originalSections);
assert.equal(merged.length, 1);
assert.equal(merged[0].section_title, 'Sheet1');
assert.equal(merged[0].metadata.records.length, 2);
assert.deepEqual(merged[0].metadata.display_section_ids, ['1', '2']);
assert.equal(originalSections[0].metadata.records.length, 1);
assert.ok(!window.ConfluenceRenderer.renderExcel(merged[0]).includes('max-height:650px'));
