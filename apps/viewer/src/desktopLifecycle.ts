import { flushSync } from 'react-dom'

export type CloseSnapshot = {
  busy: boolean
  unsavedEditor: boolean
  flush: () => Promise<unknown>
}

declare global {
  interface Window {
    estrattoreDesktop?: {
      prepareClose: () => Promise<{ ok: boolean; unsavedEditor?: boolean; message?: string }>
      cancelClose: () => void
    }
  }
}

export function installDesktopLifecycle(snapshot: () => CloseSnapshot, showClosing: (value: boolean) => void) {
  const root = document.getElementById('root')
  let preparing = false
  const cancelClose = () => {
    preparing = false
    if (root) root.inert = false
    showClosing(false)
  }
  window.estrattoreDesktop = {
    cancelClose,
    async prepareClose() {
      if (preparing) return { ok: false, message: 'Chiusura gia in corso.' }
      preparing = true
      // This native integration needs React's commit, not a timing heuristic.
      flushSync(() => {
        if (document.activeElement instanceof HTMLElement) document.activeElement.blur()
        showClosing(true)
      })
      if (root) root.inert = true
      try {
        const state = snapshot()
        if (state.busy) return { ok: false, message: 'Attendi la fine dell\'operazione in corso prima di chiudere.' }
        await state.flush()
        return { ok: true, unsavedEditor: state.unsavedEditor }
      } catch {
        return { ok: false, message: 'Non riesco a salvare le ultime modifiche. L\'app rimane aperta: riprova.' }
      }
    },
  }
  return () => {
    cancelClose()
    delete window.estrattoreDesktop
  }
}
