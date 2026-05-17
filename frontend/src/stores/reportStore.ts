import { defineStore } from 'pinia'
import { reactive } from 'vue'

export const useReportStore = defineStore('report', () => {
  const report = reactive({ content: '', sections: [] as { name: string; content: string }[] })

  function setReport(content: string) {
    report.content = content
  }

  function patchSection(section: string, content: string, append: boolean) {
    const idx = report.sections.findIndex(s => s.name === section)
    if (idx >= 0) {
      if (append) {
        report.sections[idx].content += '\n' + content
      } else {
        report.sections[idx].content = content
      }
    } else {
      report.sections.push({ name: section, content })
    }
    report.content = report.sections.map(s => s.content).join('\n\n')
  }

  function reset() {
    report.content = ''
    report.sections = []
  }

  return { report, setReport, patchSection, reset }
})
