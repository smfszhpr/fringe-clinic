(() => {
    panel.remove();
    if (sid) $('log').replaceChildren();
    let previousSpeech;
    const originalView = view;
    view = function (state) {
        const speech = state.patient?.speech;
        const display = speech === previousSpeech
            ? {...state, patient: {...state.patient, speech: ''}} : state;
        originalView(display);
        previousSpeech = speech;
        current = state;
    };
    let revision = -1;
    let running = false;
    let ended = false;
    let lastJob = null;
    let refreshing = false;
    const container = document.createElement('div');
    container.className = 'timeline';
    const heading = document.createElement('strong');
    heading.textContent = '诊疗时间线';
    const queue = document.createElement('div');
    const progress = document.createElement('p');
    progress.setAttribute('role', 'status');
    const advanceButton = document.createElement('button');
    advanceButton.textContent = '推进到下一事件';
    const dischargeButton = document.createElement('button');
    dischargeButton.textContent = '出院并结束本局';
    dischargeButton.className = 'secondary';
    container.append(heading, queue, progress, advanceButton, dischargeButton);
    $('input').before(container);
    $('action').textContent = '提交行动';
    $('confirm').textContent = '执行助手建议';
    $('input').placeholder = '直接下达问诊、检查或治疗行动，系统将自动安排执行时间';

    const ending = document.createElement('dialog');
    ending.style.cssText = 'background:#142a35;color:#e1ecef;border:1px solid #4c6976;border-radius:12px;width:min(780px,95vw);max-height:85vh;padding:30px';
    const endingTitle = document.createElement('h2');
    const narrative = document.createElement('p');
    const revealed = document.createElement('p');
    revealed.style.color = '#91dfcc';
    narrative.style.cssText = 'white-space:pre-wrap;line-height:1.95';
    const notes = document.createElement('small');
    const close = document.createElement('button');
    close.textContent = '关闭';
    close.onclick = () => ending.close();
    ending.append(endingTitle, revealed, narrative, notes, document.createElement('hr'), close);
    document.body.append(ending);
    let outcome;

    function showEnding() {
        endingTitle.textContent = outcome.title || '出院后';
        narrative.textContent = outcome.narrative || '本局已结束。';
        revealed.textContent = outcome.diagnosisReveal ? '病因揭示 · ' + outcome.diagnosisReveal : '';
        notes.textContent = (outcome.assumptions || []).length
            ? '故事中的生活补充假设：' + outcome.assumptions.join('；') : '';
        if (!ending.open) ending.showModal();
    }
    const originalControls = controls;
    controls = function () {
        originalControls();
        for (const id of ['action', 'patient', 'confirm']) {
            $(id).disabled = busy || running || ended || !sid;
        }
        advanceButton.disabled = busy || running || ended || !sid;
        dischargeButton.disabled = busy || running || !sid;
    };

    function render(result) {
        running = result.job?.status === 'running';
        ended = Boolean(result.outcome);
        queue.replaceChildren();
        const labels = {
            scheduled: '执行中', awaiting_report: '等待报告', completed: '已完成',
            closed_at_discharge: '出院时关闭'
        };
        for (const item of result.queue) {
            const line = document.createElement('p');
            line.textContent = item.label + ' · ' + (labels[item.status] || item.status)
                + ' · 第 ' + item.sampleMinute + ' 分钟采集/操作完成'
                + ' · 第 ' + item.readyMinute + ' 分钟报告';
            queue.append(line);
        }
        progress.textContent = running ? '后台正在安排或推演，游戏时钟暂停，可继续查阅病历。'
            : '游戏时间只在行动等待或主动推进时流逝。';
        if (running && result.job?.operation === 'restoreBedside') {
            progress.textContent = '正在从本局已执行的操作恢复监护连接和床旁读数…';
        }
        if (revision !== result.revision) {
            revision = result.revision;
            view(result.view);
        }
        if (result.job?.status !== 'running' && result.job?.id !== lastJob) {
            lastJob = result.job?.id;
            if (result.job?.message) add(result.job.message, result.job.status === 'error' ? 'error' : '');
        }
        if (result.outcome) {
            const first = !outcome;
            outcome = result.outcome;
            dischargeButton.textContent = '查看出院后日谈';
            if (first && outcome.narrative) showEnding();
        }
        controls();
    }
    async function refresh() {
        if (!sid || refreshing) return;
        refreshing = true;
        try {
            render(await api('simulation'));
        } catch (error) {
            progress.textContent = '连接失败：' + error.message;
        } finally {
            refreshing = false;
        }
    }
    action = async function (value) {
        if (!value.trim()) throw Error('请先输入行动');
        add(value, 'action');
        render(await api('submit', {action: value, requestId: crypto.randomUUID()}));
    };
    $('action').onclick = () => task('正在提交行动', () => action($('input').value));
    $('patient').onclick = () => task('正在安排问诊', () => action('询问患者：' + $('question').value));
    advanceButton.onclick = () => task('正在推进时间', async () => render(await api('advance', {requestId: crypto.randomUUID()})));
    dischargeButton.onclick = () => {
        if (outcome) return showEnding();
        task('正在生成出院后的故事', async () => render(await api('discharge', {requestId: crypto.randomUUID()})));
    };
    setInterval(refresh, 1500);
    refresh();
})();
