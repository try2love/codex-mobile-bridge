import { UIAbility } from '@kit.AbilityKit';
import { window } from '@kit.ArkUI';

export default class EntryAbility extends UIAbility {
  onForeground(): void { this.context.eventHub.emit('bridge-foreground', true); }
  onBackground(): void { this.context.eventHub.emit('bridge-foreground', false); }
  onWindowStageCreate(windowStage: window.WindowStage): void {
    windowStage.loadContent('pages/Index');
  }
}
