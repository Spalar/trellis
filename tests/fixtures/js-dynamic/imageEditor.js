// Minimal JS fixture for dynamic-dispatch pattern tests.
// Mimics TUI patterns: commandFactory, getComponent, new Graphics, this._method().

import commandFactory from './factory';
import { componentNames } from './consts';

export const commandNames = {
  ADD_ICON: 'addIcon',
};

class Graphics {
  constructor() {
    this._createComponents();
  }

  _createComponents() {
    const cropper = this.getComponent(componentNames.CROPPER);
    this._cropper = cropper;
  }

  getComponent(name) {
    if (name === componentNames.CROPPER) {
      return new Cropper();
    }
    return null;
  }

  add() {
    return 1;
  }

  ping() {
    return 'pong';
  }
}

class Cropper {}
class Ui {}

class ImageEditor {
  constructor(wrapper) {
    this._graphics = new Graphics(wrapper);
    this.ui = new Ui();
  }

  addIcon() {
    this._graphics.ping();
    return this.execute(commandNames.ADD_ICON, this._graphics);
  }

  execute(name, graphics) {
    const command = commandFactory.create(name, graphics);
    return command.execute(graphics);
  }
}

const addIconCommand = {
  name: commandNames.ADD_ICON,
  execute(graphics) {
    return graphics.add();
  },
};

commandFactory.register(addIconCommand);

export { ImageEditor, Graphics, Cropper, Ui };
