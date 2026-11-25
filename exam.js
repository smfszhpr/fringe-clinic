(() => {
    // Coordinates share the 240 × 420 space used for visible findings.
    const regions = [
        ['头颈', '96,12 144,12 142,78 98,78'],
        ['胸部', '85,79 155,79 150,133 90,133'],
        ['腹部与骨盆', '90,134 150,134 157,208 120,224 83,208'],
        ['右上肢', '80,79 90,100 81,159 56,210 52,232 33,240 36,213 59,153 69,98'],
        ['左上肢', '160,79 171,98 181,153 204,213 207,240 188,232 184,210 159,159 150,100'],
        ['右大腿与膝', '83,208 120,224 115,292 91,292'],
        ['左大腿与膝', '120,224 157,208 149,292 125,292'],
        ['右小腿与足', '91,293 115,293 114,402 86,402'],
        ['左小腿与足', '125,293 149,293 154,402 126,402']
    ];
    function selectRegion(name) {
        $('region').value = name;
        for (const node of $('regions').children)
            node.classList.toggle('selected', node.dataset.name === name);
        if (name) $('input').value = '观察并检查患者的' + name;
    }
    for (const [name, points] of regions) {
        $('region').add(new Option(name, name));
        const area = document.createElementNS('http://www.w3.org/2000/svg', 'polygon');
        area.dataset.name = name;
        area.setAttribute('points', points);
        const label = document.createElementNS(area.namespaceURI, 'title');
        label.textContent = name;
        area.append(label);
        area.onclick = () => selectRegion(name);
        $('regions').append(area);
    }
    $('region').onchange = event => selectRegion(event.target.value);
    const renderExam = state => {
        $('injuryList').replaceChildren();
        for (const item of state.appearance?.injuries || []) {
            const line = document.createElement('div');
            line.textContent = typeof item === 'string' ? item : item.label || item.description || item.location || '可见异常';
            $('injuryList').append(line);
        }
    };
    const renderView = view;
    view = state => { renderView(state); renderExam(state); };
    renderExam(current);
    if (sid) api('chart').then(({chart}) => {
        $('patientName').textContent = [chart['姓名'], chart['性别'], chart['年龄']].filter(Boolean).join(' · ');
    }).catch(() => { $('patientName').textContent = '病历暂不可用'; });
    $('chartTools').prepend($('start'));
})();
