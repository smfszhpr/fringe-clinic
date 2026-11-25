(() => {
    const addElement = (parent, tag, label) => {
        const node = document.createElement(tag);
        if (label) node.textContent = label;
        parent.append(node);
        return node;
    };
    const button = document.createElement('button');
    button.className = 'secondary';
    button.textContent = '诊断板';
    $('review').before(button);
    const dialog = addElement(document.body, 'dialog');
    dialog.style.cssText = 'background:#142a35;color:#e1ecef;border:1px solid #4c6976;border-radius:12px;width:min(760px,95vw);max-height:85vh;padding:24px';
    addElement(dialog, 'h2', '诊断板');
    addElement(dialog, 'p', '记录你正在考虑的解释，标记哪些发现支持或反对它。');
    const list = addElement(dialog, 'div');
    const error = addElement(dialog, 'p');
    error.setAttribute('role', 'status');
    const extra = addElement(dialog, 'button', '新增假设');
    const save = addElement(dialog, 'button', '保存诊断板');
    const close = addElement(dialog, 'button', '关闭');
    close.onclick = () => dialog.close();
    let snapshot;
    const rows = () => [...list.querySelectorAll('fieldset')];
    function findingText(finding) {
        if (typeof finding !== 'object' || finding === null) return text(finding);
        const {clueId, sampleMinute, ...display} = finding;
        return text(display);
    }

    function renderEvidence(records) {
        const container = $('evidence');
        container.replaceChildren();
        if (!records.length) addElement(container, 'small', '暂无已获取证据');
        for (const record of records) {
            const row = addElement(container, 'div');
            row.className = 'evidence';
            addElement(row, 'strong', record.id + ' · 第 ' + record.round + ' 轮');
            const sampled = record.sampleMinute === null ? '' : ' · 采集于 ' + record.sampleMinute + ' 分钟';
            addElement(row, 'small', ' 回报于 ' + record.reportedMinute + ' 分钟' + sampled);
            addElement(row, 'div', findingText(record.finding));
        }
    }

    function appendHypothesis(item = {}) {
        const field = addElement(list, 'fieldset');
        field.style.marginBottom = '12px';
        addElement(field, 'legend', '诊断假设');
        const name = addElement(addElement(field, 'label', '假设 '), 'input');
        name.dataset.field = 'name';
        name.value = item.name || '';
        name.maxLength = 120;
        const question = addElement(addElement(field, 'label', '待验证问题 '), 'input');
        question.dataset.field = 'question';
        question.value = item.question || '';
        question.maxLength = 400;
        for (const record of snapshot.evidence) {
            const row = addElement(field, 'div');
            row.className = 'evidence';
            const label = addElement(row, 'label', record.id + ' · ' + findingText(record.finding));
            const select = addElement(label, 'select');
            select.dataset.evidence = record.id;
            for (const [key, caption] of [['', '未引用'], ['supports', '支持'], ['against', '反对']]) {
                const choice = addElement(select, 'option', caption);
                choice.value = key;
            }
            select.value = item.supports?.includes(record.id) ? 'supports'
                : item.against?.includes(record.id) ? 'against' : '';
        }
        const remove = addElement(field, 'button', '移除此假设');
        remove.onclick = () => field.remove();
    }

    const baseView = view;
    view = function (state) {
        baseView(state);
        api('diagnosis').then(data => renderEvidence(data.evidence)).catch(() => {});
    };
    button.onclick = async () => {
        if (!sid) return add('接诊后可以使用诊断板。');
        try {
            snapshot = await api('diagnosis');
            list.replaceChildren();
            error.textContent = '';
            snapshot.hypotheses.forEach(appendHypothesis);
            if (!rows().length) appendHypothesis();
            renderEvidence(snapshot.evidence);
            dialog.showModal();
        } catch (failure) {
            add(failure.message, 'error');
        }
    };
    extra.onclick = () => {
        if (rows().length < 6) appendHypothesis();
    };
    save.onclick = async () => {
        const hypotheses = rows().map(row => {
            const item = Object.fromEntries([...row.querySelectorAll('[data-field]')]
                .map(input => [input.dataset.field, input.value]));
            item.supports = [];
            item.against = [];
            for (const select of row.querySelectorAll('select')) {
                if (select.value) item[select.value].push(select.dataset.evidence);
            }
            return item;
        });
        save.disabled = true;
        try {
            await api('hypotheses', {revision: snapshot.revision, hypotheses});
            dialog.close();
        } catch (failure) {
            error.textContent = failure.message;
        } finally {
            save.disabled = false;
        }
    };
})();
