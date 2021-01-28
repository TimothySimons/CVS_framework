import copy
import functools
import multiprocessing
import time

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
import torch.utils.data as data
from torch.optim import lr_scheduler
from torchvision import datasets, models, transforms
from torch.utils.data import DataLoader


class MLP(nn.Module):

    def __init__(self, dims):
        super(MLP, self).__init__()
        self.linears = nn.ModuleList()
        for i in range(len(dims)-1):
            self.linears.append(nn.Linear(dims[i], dims[i+1]))

    def forward(self, x):
        batch_size = x.shape[0]
        x = x.view(batch_size, -1)
        for i in range(len(self.linears)-1):
            x = self.linears[i](x)
            x = F.relu(x)
        return F.softmax(self.linears[-1](x), dim=1)


class CNN(nn.Module):

    def __init__(self, conv_dims, linear_dims, conv_ks, pool_ks):
        super(CNN, self).__init__()
        self.pool_ks = pool_ks
        self.convs = nn.ModuleList()
        for i in range(len(conv_dims)-1):
            conv = nn.Conv2d(conv_dims[i], conv_dims[i+1], conv_ks) 
            self.convs.append(conv)
        self.linears = nn.ModuleList()
        for i in range(len(linear_dims)-1):
            linear = nn.Linear(linear_dims[i], linear_dims[i+1])
            self.linears.append(linear)

    def forward(self, x):
        batch_size = x.shape[0]
        for i in range(len(self.convs)):
            x = self.convs[i](x) 
            x = F.max_pool2d(x, kernel_size=self.pool_ks)
        x = x.view(batch_size, -1)
        for i in range(len(self.linears)-1):
            x = self.linears[i](x)
            x = F.relu(x)
        return F.softmax(self.linears[-1](x), dim=1)


def existing(model_name, num_classes, pretrained=False, feature_extract=False):
    model =  eval(f'models.{model_name}(pretrained={pretrained})')
    if 'resnet' in model_name:
        in_features = model.fc.in_features
        model.fc = nn.Linear(in_features, num_classes)
    elif 'alexnet' in model_name or 'vgg' in model_name:
        in_features = model.classifier[6].in_features
        model.classifier[6] = nn.Linear(in_features, num_classes)
    elif 'squeezenet' in model_name:
        in_features = model.classifier[1].in_features
        model.classifier[1] = nn.Conv2d(in_features, num_classes, 
                kernel_size=(1,1), stride=(1,1))
    elif 'densenet' in model_name:
        in_features = model.classifier[1].in_features
        model.classifier = nn.Linear(in_features, num_classes)
    else:
        raise NotImplementedError(f'{model_name} not supported')
    _requires_grad(model, feature_extract)
    return model


def _requires_grad(model, feature_extract):
    if feature_extract:
        for param in model.parameters():
            param.requires_grad = False


def data_loader(data_dir, batch_size, data_transform=None): 
    dataset = datasets.ImageFolder(root=data_dir, transform=data_transform)
    num_procs = multiprocessing.cpu_count() - 1
    loader = DataLoader(dataset, batch_size, shuffle=True, 
            num_workers=num_procs)
    return len(dataset), loader


def data_transform(**kwargs):
    data_transforms = []
    for transform_name, args in kwargs.items():
        expr = f"transforms.{transform_name}{args}"
        data_transforms.append(eval(expr))
    return transforms.Compose(data_transforms)


def train(model, train_loader, val_loader, train_size, val_size, criterion, 
        optimizer, scheduler, num_epochs, device='cpu'):
    device = torch.device('cuda:0' if device == 'gpu' else 'cpu')
    model = model.to(device)
    criterion = criterion.to(device)
    best_model_wts = copy.deepcopy(model.state_dict())
    best_acc = 0.0
    for epoch in range(num_epochs):
        print(f'Epoch: {epoch}/{num_epochs-1}')
        print('-' * 10)
        epoch_loss, epoch_acc = epoch_train(model, train_loader, train_size, 
                criterion, optimizer, scheduler, device)
        print(f'Train Loss: {epoch_loss:.4f} Acc: {epoch_acc:.4f}')
        epoch_loss, epoch_acc = epoch_eval(model, val_loader, val_size, 
                criterion, device)
        print(f'Val. Loss: {epoch_loss:.4f} Acc: {epoch_acc:.4f}')
        if epoch_acc > best_acc:
            best_acc = epoch_acc
            best_model_wts = copy.deepcopy(model.state_dict())
        print()
    print(f'Best Val. Acc: {best_acc:4f}')
    model.load_state_dict(best_model_wts)
    if device == 'gpu':
        model = model.to(torch.device('cpu'))
    return model


def epoch_train(model, loader, size, criterion, optimizer, scheduler, device):
    model.train()  
    running_loss = 0.0
    running_corrects = 0
    for inputs, labels in loader: 
        inputs = inputs.to(device)
        labels = labels.to(device)
        optimizer.zero_grad()
        outputs = model(inputs)
        _, preds = torch.max(outputs, 1)
        loss = criterion(outputs, labels)
        loss.backward()
        optimizer.step()
        running_loss += loss.item() * inputs.size(0)
        running_corrects += torch.sum(preds == labels.data)
    scheduler.step()
    epoch_loss = running_loss / size
    epoch_acc = running_corrects.double() / size 
    return epoch_loss, epoch_acc


def epoch_eval(model, loader, size, criterion, device):
    model.eval()  
    running_loss = 0.0
    running_corrects = 0
    with torch.no_grad():
        for inputs, labels in loader: 
            inputs = inputs.to(device)
            labels = labels.to(device)
            outputs = model(inputs)
            _, preds = torch.max(outputs, 1)
            loss = criterion(outputs, labels)
            running_loss += loss.item() * inputs.size(0)
            running_corrects += torch.sum(preds == labels.data)
    epoch_loss = running_loss / size
    epoch_acc = running_corrects.double() / size 
    return epoch_loss, epoch_acc


if __name__ == '__main__':
    train_dir = 'orchard_data/train/'
    val_dir = 'orchard_data/val/'
    test_dir = 'orchard_data/test/'
    # ----- 1 ----- #
    transform = data_transform(
            Resize=(70,), 
            CenterCrop=(64,),
            ToTensor=(),
            )
    size, loader = data_loader(train_dir, 1000, transform)
    data, _ = next(iter(loader))
    means = [data[:,c].mean().tolist() for c in range(len(data[0]))] 
    stds = [data[:,c].std().tolist() for c in range(len(data[0]))]

    # ----- 2 ----- #
    batch_size = 4
    transform = data_transform(
        Resize=(70,),
        CenterCrop=(64,),
        ToTensor=(),
        Normalize=(means, stds),
        )
    train_size, train_loader = data_loader(train_dir, batch_size, transform)
    print(train_size)
    val_size, val_loader = data_loader(val_dir, batch_size, transform)

    # ----- 3 ----- #   
    #nn_model = nn_classifier.MLP([150528, 250, 2])
    #nn_model = nn_classifier.CNN([3, 6, 16], [16 * 53 * 53, 120, 84, 2], conv_ks=5, pool_ks=2)
    #nn_model = nn_classifier.existing_model('resnet18', 2)
    nn_model = existing('inception_v3', 5)
    criterion = nn.CrossEntropyLoss()
    optimizer = optim.SGD(nn_model.parameters(), lr=0.001, momentum=0.9)
    exp_lr_scheduler = lr_scheduler.StepLR(optimizer, step_size=7, gamma=0.1)
    nn_model = train(
            nn_model, 
            train_loader, val_loader, 
            train_size, val_size, 
            criterion, 
            optimizer,
            exp_lr_scheduler, 
            num_epochs=1,
            )





    