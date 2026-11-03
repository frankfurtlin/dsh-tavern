"""Deterministic prose fixtures only for the explicitly configured mock adapter."""
import json

def result(prompt, data):
    if prompt.startswith('prose-review-'):
        return json.dumps({'completeChapter': True, 'issues': []})
    chapter = data['chapter']
    index = chapter['index']
    opening = f'标题：第 {index} 章 · 栈桥上的证言\n\n林渡把新的纸页放在桌上。第{index}次核对已经开始，他先将眼前的事与记忆里的细节逐一对齐。'
    target = {'short': 1000, 'standard': 1800, 'long': 3000}[chapter['length']]
    paragraphs = [opening]
    counter = 0
    while len(''.join(paragraphs)) < target:
        counter += 1
        paragraphs.append(f'第{counter}处记录注明了不同的时刻。林渡沿着栈桥走到对应的木柱，把灯放稳，再请守夜人指明当时站立的位置。对方没有立刻答应，只先检查自己留下的记号。两人对照纸页上的笔迹，发现其中一笔方向与其余记录相反。林渡把这一处单独标出，让尚未核实的推测留在纸边，继续向下一位见证者询问。')
    paragraphs.append('核对完成后，林渡将确认的证言放进干燥的盒子，另把仍有疑问的纸页留下。今晚的调查有了可以复查的结果，下一次出发也有了明确的线索。他吹灭桌上的灯，先去把新的发现告诉等候的人。')
    return '\n\n'.join(paragraphs)
